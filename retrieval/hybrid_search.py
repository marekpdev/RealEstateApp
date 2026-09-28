import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[str]],
    k: int,
) -> List[Tuple[str, float]]:
    """
    score(d) = sum, over every ranked list containing d, of 1 / (k + rank_i(d)),
    where rank_i is d's 1-based position in that list. An item absent from a
    list simply contributes no term for that list - RRF never needs a
    penalty value or a comparable score scale from either input list, which
    is exactly why it can fuse two rankings produced by unrelated scoring
    functions (Pinecone's cosine/dot-product similarity, Postgres's
    ts_rank_cd) without normalizing either one first.

    Returns (key, score) pairs sorted by score descending. Ties are broken
    by Python's stable sort, so equal-scoring items keep the relative order
    they were first seen in across the input lists.
    """
    scores: Dict[str, float] = {}
    for ranked_list in ranked_lists:
        for rank, key in enumerate(ranked_list, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


@dataclass(frozen=True)
class ChunkResult:
    content: str
    source: str


@dataclass(frozen=True)
class FusedChunk:
    content: str
    source: str
    rrf_score: float
    dense_rank: Optional[int]
    lexical_rank: Optional[int]


@dataclass(frozen=True)
class HybridSearchOutcome:
    fused: List[FusedChunk]
    dense_error: Optional[str]
    lexical_error: Optional[str]


async def _dense_search(vectorstore: Any, query: str, k: int) -> List[ChunkResult]:
    # vectorstore.similarity_search() is a synchronous, blocking network
    # call (the same one tools/vector_tools.py's dense-only search_zoning_laws
    # already makes) - run it off the event loop via to_thread so it can
    # genuinely run concurrently with the lexical Postgres query below,
    # rather than blocking the whole graph's event loop for its duration.
    docs = await asyncio.to_thread(vectorstore.similarity_search, query, k)
    return [
        ChunkResult(content=doc.page_content, source=doc.metadata.get("source", "Unknown Archive"))
        for doc in docs
    ]


async def _lexical_search(query: str, k: int) -> List[ChunkResult]:
    # Imported here, not at module level, so importing this module never
    # requires db/ to be importable - only actually calling the lexical
    # half does. session_scope() opens its own unit of work and is safe to
    # call from whichever event loop is already running (the same one the
    # graph run is on) - a fresh asyncio.run() call here instead would risk
    # handing this call db/session.py's process-wide engine singleton with
    # pooled connections bound to a *different*, already-closed loop.
    from db.repositories.document_chunk_repository import DocumentChunkRepository
    from db.session import session_scope

    async with session_scope() as session:
        rows = await DocumentChunkRepository(session).search_lexical(query, k=k)
    return [ChunkResult(content=chunk.content, source=chunk.source_origin) for chunk, _rank in rows]


async def hybrid_search_zoning_documents(
    query: str,
    *,
    vectorstore: Any,
    candidates_per_source: int,
    results: int,
    rrf_k: int,
    dense_skip_reason: Optional[str] = None,
) -> HybridSearchOutcome:
    """
    Runs Pinecone's dense search and Postgres's lexical search in parallel
    and fuses the two ranked lists with Reciprocal Rank Fusion, joining an
    item across both lists by its exact chunk content - the same text both
    indexes were populated from in the first place (see
    scripts/sync_knowledge_base.py's own chunk-parity design), so there is
    no separate id scheme to reconcile between a Pinecone-generated id and
    this table's own UUID primary key.

    Pass vectorstore=None (with dense_skip_reason explaining why - "not
    configured", "circuit breaker open", ...) to skip the dense half
    entirely without attempting a network call; a genuine failure from an
    attempted call instead surfaces as HybridSearchOutcome.dense_error. The
    lexical half degrades the same way through HybridSearchOutcome.
    lexical_error rather than raising, so one side's outage never prevents
    the other from being returned.
    """
    lexical_coro = _lexical_search(query, candidates_per_source)

    if vectorstore is None:
        dense_chunks: List[ChunkResult] = []
        dense_error: Optional[str] = dense_skip_reason or "Pinecone is not configured."
        lexical_outcome = await asyncio.gather(lexical_coro, return_exceptions=True)
        lexical_result = lexical_outcome[0]
    else:
        dense_coro = _dense_search(vectorstore, query, candidates_per_source)
        dense_outcome, lexical_result = await asyncio.gather(
            dense_coro, lexical_coro, return_exceptions=True
        )
        if isinstance(dense_outcome, Exception):
            dense_chunks = []
            dense_error = str(dense_outcome)
        else:
            dense_chunks = dense_outcome
            dense_error = None

    if isinstance(lexical_result, Exception):
        lexical_chunks: List[ChunkResult] = []
        lexical_error: Optional[str] = str(lexical_result)
    else:
        lexical_chunks = lexical_result
        lexical_error = None

    dense_keys = [chunk.content for chunk in dense_chunks]
    lexical_keys = [chunk.content for chunk in lexical_chunks]
    chunk_by_key = {chunk.content: chunk for chunk in (*dense_chunks, *lexical_chunks)}
    dense_rank_by_key = {key: rank for rank, key in enumerate(dense_keys, start=1)}
    lexical_rank_by_key = {key: rank for rank, key in enumerate(lexical_keys, start=1)}

    fused_scores = reciprocal_rank_fusion([dense_keys, lexical_keys], k=rrf_k)

    fused = [
        FusedChunk(
            content=chunk_by_key[key].content,
            source=chunk_by_key[key].source,
            rrf_score=score,
            dense_rank=dense_rank_by_key.get(key),
            lexical_rank=lexical_rank_by_key.get(key),
        )
        for key, score in fused_scores[:results]
    ]

    return HybridSearchOutcome(fused=fused, dense_error=dense_error, lexical_error=lexical_error)
