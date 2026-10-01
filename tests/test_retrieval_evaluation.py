import pytest

from db.repositories import DocumentChunkRepository
from retrieval.eval_dataset import EVAL_CORPUS, EVAL_QUERIES, relevant_contents
from retrieval.evaluation import (
    MetricScores,
    aggregate,
    ndcg_at_k,
    reciprocal_rank,
    recall_at_k,
    score_query,
)
from scripts.evaluate_retrieval import _DemoVectorStore, run_evaluation


# --- pure metric functions: no I/O, hand-computed expected values ----------


def test_recall_at_k_counts_the_fraction_of_relevant_items_found():
    retrieved = ["a", "b", "c", "d"]
    relevant = frozenset({"b", "d", "z"})
    # Only "b" is within the top 3 ("d" is 4th, past the cutoff; "z" never
    # shows up at all) - 1 of the 3 relevant items found.
    assert recall_at_k(retrieved, relevant, k=3) == pytest.approx(1 / 3)
    # Widening the cutoff to include "d" recovers the second relevant item.
    assert recall_at_k(retrieved, relevant, k=4) == pytest.approx(2 / 3)


def test_recall_at_k_with_no_relevant_items_is_zero_not_a_division_error():
    assert recall_at_k(["a", "b"], frozenset(), k=3) == 0.0


def test_recall_at_k_ignores_anything_past_the_cutoff():
    retrieved = ["irrelevant", "irrelevant", "irrelevant", "target"]
    assert recall_at_k(retrieved, frozenset({"target"}), k=3) == 0.0
    assert recall_at_k(retrieved, frozenset({"target"}), k=4) == 1.0


def test_reciprocal_rank_is_one_over_the_first_relevant_positions_rank():
    assert reciprocal_rank(["a", "b", "c"], frozenset({"c"})) == pytest.approx(1 / 3)
    assert reciprocal_rank(["a", "b", "c"], frozenset({"a", "c"})) == pytest.approx(1.0)


def test_reciprocal_rank_is_zero_when_nothing_relevant_was_retrieved():
    assert reciprocal_rank(["a", "b"], frozenset({"z"})) == 0.0


def test_ndcg_at_k_is_perfect_when_every_relevant_item_is_packed_at_the_top():
    retrieved = ["a", "b", "c"]
    relevant = frozenset({"a", "b"})
    assert ndcg_at_k(retrieved, relevant, k=3) == pytest.approx(1.0)


def test_ndcg_at_k_penalizes_a_relevant_item_ranked_lower():
    # Same single relevant item ("z"), found at rank 1 versus rank 3 - the
    # rank-3 case must score strictly lower, which is the entire point of a
    # *discounted* cumulative gain over a plain hit/miss count.
    high = ndcg_at_k(["z", "a", "b"], frozenset({"z"}), k=3)
    low = ndcg_at_k(["a", "b", "z"], frozenset({"z"}), k=3)
    assert high == pytest.approx(1.0)
    assert 0.0 < low < high


def test_ndcg_at_k_with_no_relevant_items_is_zero_not_a_division_error():
    assert ndcg_at_k(["a", "b"], frozenset(), k=3) == 0.0


def test_score_query_bundles_all_three_metrics():
    scores = score_query(["a", "b"], frozenset({"a"}), k=2)
    assert scores == MetricScores(recall_at_k=1.0, mrr=1.0, ndcg_at_k=1.0)


def test_aggregate_averages_each_metric_across_queries():
    scores = [
        MetricScores(recall_at_k=1.0, mrr=1.0, ndcg_at_k=1.0),
        MetricScores(recall_at_k=0.0, mrr=0.0, ndcg_at_k=0.0),
    ]
    assert aggregate(scores) == MetricScores(recall_at_k=0.5, mrr=0.5, ndcg_at_k=0.5)


def test_aggregate_of_an_empty_list_is_zero_not_a_division_error():
    assert aggregate([]) == MetricScores(recall_at_k=0.0, mrr=0.0, ndcg_at_k=0.0)


# --- the eval dataset itself: every judgment must resolve to a real chunk --


def test_every_eval_query_references_indices_that_exist_in_the_corpus():
    for eval_query in EVAL_QUERIES:
        for index in eval_query.relevant_indices:
            assert 0 <= index < len(EVAL_CORPUS)


def test_relevant_contents_resolves_indices_to_the_corpuss_own_chunk_text():
    query = EVAL_QUERIES[0]
    resolved = relevant_contents(query)
    assert resolved == frozenset(EVAL_CORPUS[i]["content"] for i in query.relevant_indices)


# --- run_evaluation: real Postgres lexical search, a stand-in dense arm ----


@pytest.mark.asyncio
async def test_run_evaluation_lexical_only_finds_nothing_for_a_paraphrased_query(db_session):
    """The concrete, surprising finding this whole harness exists to catch:
    plain lexical search doesn't just rank a paraphrased query's relevant
    chunk lower, it finds *zero* results for several of this eval set's
    paraphrased queries at all, because websearch_to_tsquery ANDs every
    bare query term together - one query word the source chunk doesn't
    happen to share drops that chunk from the results entirely. Verified
    directly against real Postgres, not asserted from a mock."""
    await DocumentChunkRepository(db_session).replace_all(EVAL_CORPUS)
    await db_session.commit()

    per_method = await run_evaluation(
        db_session,
        vectorstore=None,
        dense_skip_reason="Pinecone is not configured.",
        k=3,
        candidates_per_source=10,
        rrf_k=60,
    )

    paraphrased_indices = [i for i, q in enumerate(EVAL_QUERIES) if not q.favors_lexical]
    assert paraphrased_indices, "fixture must include at least one paraphrased query"
    for i in paraphrased_indices:
        assert per_method["lexical-only"][i].recall_at_k == 0.0

    exact_term_indices = [i for i, q in enumerate(EVAL_QUERIES) if q.favors_lexical]
    assert exact_term_indices, "fixture must include at least one exact-term query"
    assert any(per_method["lexical-only"][i].recall_at_k > 0.0 for i in exact_term_indices)


@pytest.mark.asyncio
async def test_run_evaluation_hybrid_recovers_what_lexical_alone_misses(db_session):
    """The property this harness exists to demonstrate, using the
    hand-authored demo dense stand-in (see retrieval/eval_dataset.py) in
    place of real Pinecone: hybrid's aggregate recall@k across the
    paraphrased queries must exceed lexical-only's, because RRF's fused
    ranking includes whatever the dense arm found even when lexical
    returned nothing for that query at all."""
    await DocumentChunkRepository(db_session).replace_all(EVAL_CORPUS)
    await db_session.commit()

    per_method = await run_evaluation(
        db_session,
        vectorstore=_DemoVectorStore(),
        dense_skip_reason=None,
        k=3,
        candidates_per_source=10,
        rrf_k=60,
    )

    paraphrased_indices = [i for i, q in enumerate(EVAL_QUERIES) if not q.favors_lexical]
    lexical_recall = aggregate([per_method["lexical-only"][i] for i in paraphrased_indices]).recall_at_k
    hybrid_recall = aggregate([per_method["hybrid (dense+lexical, RRF)"][i] for i in paraphrased_indices]).recall_at_k

    assert lexical_recall == 0.0
    assert hybrid_recall > lexical_recall


@pytest.mark.asyncio
async def test_documented_demo_results_match_what_the_harness_produces(db_session):
    """docs/AGENTIC_AI.md shows the `--demo-dense` results as a table, and
    docs/images/generate.py draws its nDCG@3 chart from the same numbers.
    Pinning them here means the documentation cannot silently go stale when
    the eval set, the fusion code or a metric changes: this test fails, and
    the table and the chart get regenerated together."""
    await DocumentChunkRepository(db_session).replace_all(EVAL_CORPUS)
    await db_session.commit()

    per_method = await run_evaluation(
        db_session,
        vectorstore=_DemoVectorStore(),
        dense_skip_reason=None,
        k=3,
        candidates_per_source=10,
        rrf_k=60,
    )

    everything = list(range(len(EVAL_QUERIES)))
    exact_term = [i for i, q in enumerate(EVAL_QUERIES) if q.favors_lexical]
    paraphrased = [i for i, q in enumerate(EVAL_QUERIES) if not q.favors_lexical]
    hybrid = "hybrid (dense+lexical, RRF)"

    # (recall@3, MRR, nDCG@3), as printed by the run.
    documented = {
        ("lexical-only", "overall"): (0.438, 0.500, 0.452),
        (hybrid, "overall"): (1.000, 1.000, 1.000),
        ("dense-only", "overall"): (1.000, 0.875, 0.908),
        ("lexical-only", "exact-term"): (0.875, 1.000, 0.903),
        (hybrid, "exact-term"): (1.000, 1.000, 1.000),
        ("dense-only", "exact-term"): (1.000, 0.750, 0.815),
        ("lexical-only", "paraphrased"): (0.000, 0.000, 0.000),
        (hybrid, "paraphrased"): (1.000, 1.000, 1.000),
        ("dense-only", "paraphrased"): (1.000, 1.000, 1.000),
    }
    groups = {"overall": everything, "exact-term": exact_term, "paraphrased": paraphrased}
    for (method, group), expected in documented.items():
        got = aggregate([per_method[method][i] for i in groups[group]])
        assert (got.recall_at_k, got.mrr, got.ndcg_at_k) == pytest.approx(expected, abs=0.001), (method, group)
