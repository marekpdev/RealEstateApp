# file: scripts/evaluate_retrieval.py
"""
Scores dense-only, lexical-only and hybrid (RRF-fused) retrieval against a
small, hand-labelled query -> relevant-chunk evaluation set
(retrieval/eval_dataset.py), using recall@k, MRR and nDCG@k
(retrieval/evaluation.py). This is the only real defence against tuning
retrieval by feel: without a fixed, hand-labelled set of "here is what a
good result looks like for this query," a change to chunking, RRF's k, or
which side gets queried first can look like an improvement in a demo and
be a regression in general - there is no way to tell without measuring
against the same fixed queries before and after.

Runs the entire evaluation inside one Postgres transaction that is rolled
back at the end, never committed - re-seeding document_chunks with this
module's own synthetic corpus would otherwise silently overwrite whatever
scripts/sync_knowledge_base.py's own scheduled resync last wrote there,
which is the real lexical index the live Zoning Law agent actually queries
in the running application. See db/session.py's module-level engine/
sessionmaker singletons and tests/conftest.py's own db_session fixture,
whose exact transaction+SAVEPOINT-rollback shape this reuses as a runtime
technique here, not just a testing one.

Usage:
    uv run python scripts/evaluate_retrieval.py
    uv run python scripts/evaluate_retrieval.py --demo-dense
"""
import argparse
import asyncio
from dataclasses import dataclass
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import async_sessionmaker

from config.config import RETRIEVAL_EVAL_CANDIDATES_PER_SOURCE, RETRIEVAL_EVAL_K
from db import session as db_session
from db.repositories import DocumentChunkRepository
from retrieval.eval_dataset import (
    DEMO_DENSE_RESULTS_BY_QUERY,
    EVAL_CORPUS,
    EVAL_QUERIES,
    EvalQuery,
    relevant_contents,
)
from retrieval.evaluation import MetricScores, aggregate, score_query
from retrieval.hybrid_search import hybrid_search_zoning_documents
from services.vector_store import get_pinecone_vector_store


class _DemoDocument:
    """Just enough of a LangChain Document for hybrid_search.py's own
    _dense_search() to read (.page_content, .metadata["source"])."""

    def __init__(self, content: str, source: str):
        self.page_content = content
        self.metadata = {"source": source}


class _DemoVectorStore:
    """A hand-authored stand-in for a real Pinecone vectorstore - see
    retrieval/eval_dataset.py's DEMO_DENSE_RESULTS_BY_QUERY docstring for
    why this exists (this sandbox cannot reach api.pinecone.io at all) and
    what it deliberately is not (a claim about real embedding quality).
    Exposes only the one method hybrid_search_zoning_documents()'s own
    _dense_search() actually calls, so that real orchestration/fusion code
    runs completely unmodified against it - this class changes nothing
    about how dense results get fused, only where they come from."""

    def similarity_search(self, query: str, k: int) -> List[_DemoDocument]:
        ranking = DEMO_DENSE_RESULTS_BY_QUERY.get(query, [])
        return [
            _DemoDocument(EVAL_CORPUS[i]["content"], EVAL_CORPUS[i]["source_origin"])
            for i in ranking[:k]
        ]


async def _dense_only(vectorstore, query: str, k: int) -> List[str]:
    docs = await asyncio.to_thread(vectorstore.similarity_search, query, k)
    return [doc.page_content for doc in docs]


async def run_evaluation(
    session,
    *,
    vectorstore,
    dense_skip_reason: Optional[str],
    k: int,
    candidates_per_source: int,
    rrf_k: int,
) -> Dict[str, List[MetricScores]]:
    """Runs every EVAL_QUERIES entry through all three retrieval methods and
    returns each method's per-query MetricScores list (same order as
    EVAL_QUERIES) - callers aggregate() the whole list, or split it by
    EvalQuery.favors_lexical first for the category breakdown main() prints.
    Assumes document_chunks has already been seeded with EVAL_CORPUS on
    this same session/connection; does no seeding itself, so a test can
    reuse it against a db_session fixture that seeds differently.
    """
    repo = DocumentChunkRepository(session)

    per_method: Dict[str, List[MetricScores]] = {
        "lexical-only": [],
        "hybrid (dense+lexical, RRF)": [],
    }
    if vectorstore is not None:
        per_method["dense-only"] = []

    for eval_query in EVAL_QUERIES:
        relevant = relevant_contents(eval_query)

        lexical_rows = await repo.search_lexical(eval_query.query, k=candidates_per_source)
        lexical_retrieved = [chunk.content for chunk, _rank in lexical_rows]
        per_method["lexical-only"].append(score_query(lexical_retrieved, relevant, k))

        if vectorstore is not None:
            dense_retrieved = await _dense_only(vectorstore, eval_query.query, candidates_per_source)
            per_method["dense-only"].append(score_query(dense_retrieved, relevant, k))

        outcome = await hybrid_search_zoning_documents(
            eval_query.query,
            vectorstore=vectorstore,
            dense_skip_reason=dense_skip_reason,
            candidates_per_source=candidates_per_source,
            results=candidates_per_source,
            rrf_k=rrf_k,
        )
        hybrid_retrieved = [chunk.content for chunk in outcome.fused]
        per_method["hybrid (dense+lexical, RRF)"].append(score_query(hybrid_retrieved, relevant, k))

    return per_method


def _print_table(title: str, rows: Dict[str, MetricScores], k: int) -> None:
    print(f"\n{title}")
    header = f"{'method':<28} {f'recall@{k}':>10} {'mrr':>8} {f'ndcg@{k}':>9}"
    print(header)
    print("-" * len(header))
    for name, scores in rows.items():
        print(f"{name:<28} {scores.recall_at_k:>10.3f} {scores.mrr:>8.3f} {scores.ndcg_at_k:>9.3f}")


def _split_by_category(
    per_method: Dict[str, List[MetricScores]], queries: List[EvalQuery]
) -> Dict[str, Dict[str, MetricScores]]:
    """Two extra breakdown tables (favors_lexical=True/False) on top of the
    overall one - the whole reason each query is tagged that way at all is
    that an aggregate score alone would hide the actual finding: lexical
    search doesn't just rank paraphrased queries lower, it returns *zero*
    results for them (Postgres's websearch_to_tsquery ANDs every bare query
    term together - one word the source chunk doesn't happen to share drops
    it from lexical results entirely). Averaging that in with the exact-term
    queries it handles perfectly would wash the finding out."""
    categories: Dict[str, Dict[str, MetricScores]] = {}
    for favors_lexical, label in ((True, "exact-term queries"), (False, "paraphrased queries")):
        indices = [i for i, q in enumerate(queries) if q.favors_lexical is favors_lexical]
        categories[label] = {
            method: aggregate([scores[i] for i in indices]) for method, scores in per_method.items()
        }
    return categories


@dataclass(frozen=True)
class _DenseSetup:
    vectorstore: object
    skip_reason: Optional[str]
    label: str


def _resolve_dense_setup(demo_dense: bool) -> _DenseSetup:
    if demo_dense:
        return _DenseSetup(
            vectorstore=_DemoVectorStore(),
            skip_reason=None,
            label="DEMO dense arm: a hand-authored stand-in, not real Pinecone embeddings (see retrieval/eval_dataset.py)",
        )
    real_vectorstore = get_pinecone_vector_store()
    if real_vectorstore is not None:
        return _DenseSetup(vectorstore=real_vectorstore, skip_reason=None, label="real Pinecone dense search")
    return _DenseSetup(
        vectorstore=None,
        skip_reason="Pinecone is not configured in this environment.",
        label="dense search unavailable - PINECONE_API_KEY not set (or unreachable); "
        "hybrid degrades to lexical-only. Re-run with --demo-dense to see the "
        "harness score a dense-like ranking, or with a real, reachable "
        "PINECONE_API_KEY for genuine numbers.",
    )


async def _main(demo_dense: bool) -> None:
    dense_setup = _resolve_dense_setup(demo_dense)
    print(f"Dense arm: {dense_setup.label}")

    try:
        engine = db_session.get_engine()
        async with engine.connect() as connection:
            await connection.begin()
            sessionmaker = async_sessionmaker(
                bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )

            original_engine = db_session._engine
            original_sessionmaker = db_session._sessionmaker
            db_session._engine = engine
            db_session._sessionmaker = sessionmaker
            try:
                session = sessionmaker()
                try:
                    await DocumentChunkRepository(session).replace_all(EVAL_CORPUS)
                    await session.commit()

                    per_method = await run_evaluation(
                        session,
                        vectorstore=dense_setup.vectorstore,
                        dense_skip_reason=dense_setup.skip_reason,
                        k=RETRIEVAL_EVAL_K,
                        candidates_per_source=RETRIEVAL_EVAL_CANDIDATES_PER_SOURCE,
                        rrf_k=60,
                    )
                finally:
                    await session.close()
            finally:
                db_session._engine = original_engine
                db_session._sessionmaker = original_sessionmaker
                # Never committed: this evaluation's synthetic corpus must
                # not replace the live application's own synced
                # document_chunks.
                await connection.rollback()
    finally:
        # Inside this same asyncio.run() call, before its own loop closes -
        # db/session.py's engine is a process-wide singleton whose pooled
        # asyncpg connections are bound to whichever loop built them
        # (worker/tasks.py's generate_report documents this at length for
        # the identical reason).
        await db_session.dispose_engine()

    overall = {method: aggregate(scores) for method, scores in per_method.items()}
    _print_table(f"Overall ({len(EVAL_QUERIES)} queries)", overall, RETRIEVAL_EVAL_K)

    for label, methods in _split_by_category(per_method, EVAL_QUERIES).items():
        _print_table(label.capitalize(), methods, RETRIEVAL_EVAL_K)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--demo-dense",
        action="store_true",
        help=(
            "Use a hand-authored stand-in dense arm instead of real Pinecone "
            "(or instead of skipping dense entirely when Pinecone isn't "
            "configured) - see retrieval/eval_dataset.py for exactly what "
            "this does and does not represent."
        ),
    )
    args = parser.parse_args()
    asyncio.run(_main(args.demo_dense))


if __name__ == "__main__":
    main()
