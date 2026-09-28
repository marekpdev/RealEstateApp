from langchain_core.tools import tool

from config.config import (
    HYBRID_RETRIEVAL_CANDIDATES_PER_SOURCE,
    HYBRID_RETRIEVAL_RESULTS,
    HYBRID_RETRIEVAL_RRF_K,
)
from resilience.circuit_breaker import CircuitBreakerOpenError, get_circuit_breaker
from retrieval.hybrid_search import hybrid_search_zoning_documents
from services.vector_store import PINECONE_CIRCUIT_BREAKER_NAME, get_pinecone_vector_store


@tool
async def search_zoning_laws_hybrid(query: str) -> str:
    """
    Searches for zoning laws, municipal regulations and land-use policies
    using hybrid retrieval: Pinecone's dense (meaning-based) vector search
    and Postgres's lexical (keyword-based) full-text search run in
    parallel, then get fused with Reciprocal Rank Fusion - so an exact,
    rare term (an ordinance number, a specific zoning code) that dense
    search alone tends to miss is still surfaced. Use this to find verified
    regulatory data from municipal archives.
    """
    print(f"🔍 Executing hybrid (dense + lexical) zoning search for: '{query}'")

    vectorstore = get_pinecone_vector_store()
    breaker = get_circuit_breaker(PINECONE_CIRCUIT_BREAKER_NAME)

    dense_skip_reason = None
    attempted_dense = False
    if vectorstore is None:
        dense_skip_reason = "Pinecone is not configured. Please ensure PINECONE_API_KEY and PINECONE_INDEX_NAME are set."
    else:
        try:
            breaker.before_call()
            attempted_dense = True
        except CircuitBreakerOpenError as exc:
            print(f"⚡ {exc}")
            vectorstore = None
            dense_skip_reason = (
                "a circuit breaker opened after repeated Pinecone failures and "
                f"won't let another call through for about {exc.retry_after_seconds:.0f}s"
            )

    outcome = await hybrid_search_zoning_documents(
        query,
        vectorstore=vectorstore,
        dense_skip_reason=dense_skip_reason,
        candidates_per_source=HYBRID_RETRIEVAL_CANDIDATES_PER_SOURCE,
        results=HYBRID_RETRIEVAL_RESULTS,
        rrf_k=HYBRID_RETRIEVAL_RRF_K,
    )

    if attempted_dense:
        if outcome.dense_error:
            breaker.record_failure()
        else:
            breaker.record_success()

    dense_unavailable = dense_skip_reason or outcome.dense_error
    if dense_unavailable and outcome.lexical_error:
        # Neither retrieval path produced anything usable at all - the same
        # degraded-but-not-raised shape the dense-only tool already uses,
        # so the calling agent's own system prompt (already treating "no
        # results or insufficient data" as the signal to fall back to web
        # search) needs no changes to handle this too.
        return (
            "Hybrid zoning search is temporarily unavailable: dense search "
            f"failed ({dense_unavailable}) and lexical search failed "
            f"({outcome.lexical_error}). Use the web search tool for this "
            "query instead."
        )

    if not outcome.fused:
        return f"No relevant zoning laws found (hybrid dense+lexical search) for query: '{query}'."

    notices = []
    if dense_unavailable:
        notices.append(f"dense/Pinecone search unavailable ({dense_unavailable}) - showing lexical-only results")
    if outcome.lexical_error:
        notices.append(f"lexical/Postgres search unavailable ({outcome.lexical_error}) - showing dense-only results")

    formatted_results = [
        f"--- SOURCE: {chunk.source} (rrf_score={chunk.rrf_score:.4f}, "
        f"dense_rank={chunk.dense_rank}, lexical_rank={chunk.lexical_rank}) ---\n{chunk.content}"
        for chunk in outcome.fused
    ]

    print(f"✨ Hybrid search fused {len(outcome.fused)} result(s): {formatted_results}")

    body = "\n\n".join(formatted_results)
    if notices:
        return f"[{'; '.join(notices)}]\n\n{body}"
    return body
