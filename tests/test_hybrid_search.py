from unittest.mock import MagicMock

import pytest

from db.repositories.document_chunk_repository import DocumentChunkRepository
from retrieval.hybrid_search import hybrid_search_zoning_documents, reciprocal_rank_fusion


# --- reciprocal_rank_fusion: pure function, no I/O, no mocking needed ------


def test_reciprocal_rank_fusion_sums_reciprocal_ranks_across_lists():
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "a"]], k=60)
    scores = dict(fused)
    assert scores["a"] == pytest.approx(1 / 61 + 1 / 63)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["c"] == pytest.approx(1 / 63 + 1 / 62)


def test_reciprocal_rank_fusion_ranks_an_item_present_in_both_lists_above_either_lists_own_top_pick():
    # "shared" is #2 in the first list and #1 in the second - neither list's
    # own top pick ("x" / "shared") on its own would be enough to prove
    # fusion is happening, but "shared" outscoring "x" (which only ever
    # appears in one list, at rank 1) demonstrates the actual point of RRF:
    # appearing reasonably high in *both* rankings beats being the single
    # best hit in just one.
    fused = reciprocal_rank_fusion([["x", "shared"], ["shared", "y"]], k=60)
    ranked_keys = [key for key, _ in fused]
    assert ranked_keys[0] == "shared"


def test_reciprocal_rank_fusion_includes_items_present_in_only_one_list():
    fused = reciprocal_rank_fusion([["only_dense"], []], k=60)
    assert dict(fused)["only_dense"] == pytest.approx(1 / 61)


def test_reciprocal_rank_fusion_with_one_empty_list_degrades_to_the_other_lists_own_order():
    fused = reciprocal_rank_fusion([[], ["a", "b"]], k=60)
    assert [key for key, _ in fused] == ["a", "b"]


def test_reciprocal_rank_fusion_smaller_k_rewards_top_rank_more_steeply():
    # A smaller k widens the score gap between rank 1 and rank 2 - the
    # "how much k matters" question the roadmap's own doc-focus calls out.
    fused_small_k = dict(reciprocal_rank_fusion([["a", "b"]], k=1))
    fused_large_k = dict(reciprocal_rank_fusion([["a", "b"]], k=1000))
    gap_small_k = fused_small_k["a"] - fused_small_k["b"]
    gap_large_k = fused_large_k["a"] - fused_large_k["b"]
    assert gap_small_k > gap_large_k


# --- hybrid_search_zoning_documents: orchestration + degradation -----------


def _mock_doc(content: str, source: str) -> MagicMock:
    doc = MagicMock()
    doc.page_content = content
    doc.metadata = {"source": source}
    return doc


@pytest.mark.asyncio
async def test_hybrid_search_fuses_dense_and_lexical_so_the_top_result_is_not_either_sides_own_top_pick(db_session):
    # Dense's own #1 pick ("dense_only_top") and lexical's own #1 pick
    # ("lexical_only_top") each appear in only one list. "shared_chunk"
    # appears in both, at a decent (not top) rank in each - RRF should
    # still surface it above either side's individual favorite, which is
    # exactly the property the roadmap's own Verify step names: "hybrid
    # returns results neither method alone ranked first."
    shared_content = "Ordinance 12-345 establishes multi-family setback requirements for R-3 zones."
    await DocumentChunkRepository(db_session).replace_all(
        [
            {
                "source_origin": "lexical-top.pdf",
                "page": 1,
                "chunk_index": 0,
                "content": "Setback setback setback multi-family multi-family requirements downtown.",
            },
            {
                "source_origin": "shared.pdf",
                "page": 1,
                "chunk_index": 0,
                "content": shared_content,
            },
        ]
    )
    await db_session.commit()

    mock_vectorstore = MagicMock()
    mock_vectorstore.similarity_search.return_value = [
        _mock_doc("Downtown historical preservation overlay district guidelines.", "dense-top.pdf"),
        _mock_doc(shared_content, "shared.pdf"),
    ]

    outcome = await hybrid_search_zoning_documents(
        "multi-family setback requirements",
        vectorstore=mock_vectorstore,
        candidates_per_source=10,
        results=3,
        rrf_k=60,
    )

    assert outcome.dense_error is None
    assert outcome.lexical_error is None
    assert len(outcome.fused) >= 1
    top = outcome.fused[0]
    assert top.content == shared_content
    assert top.dense_rank == 2
    assert top.lexical_rank is not None


@pytest.mark.asyncio
async def test_hybrid_search_skips_dense_and_returns_lexical_only_when_vectorstore_is_none(db_session):
    await DocumentChunkRepository(db_session).replace_all(
        [{"source_origin": "a.pdf", "page": 1, "chunk_index": 0, "content": "Zoning code 55-100 governs parking minimums."}]
    )
    await db_session.commit()

    outcome = await hybrid_search_zoning_documents(
        "zoning code 55-100",
        vectorstore=None,
        dense_skip_reason="Pinecone is not configured.",
        candidates_per_source=10,
        results=3,
        rrf_k=60,
    )

    assert outcome.dense_error == "Pinecone is not configured."
    assert outcome.lexical_error is None
    assert len(outcome.fused) == 1
    assert outcome.fused[0].dense_rank is None
    assert outcome.fused[0].lexical_rank == 1


@pytest.mark.asyncio
async def test_hybrid_search_surfaces_a_dense_exception_without_raising(db_session):
    await DocumentChunkRepository(db_session).replace_all(
        [{"source_origin": "a.pdf", "page": 1, "chunk_index": 0, "content": "Zoning code 55-100 governs parking minimums."}]
    )
    await db_session.commit()

    mock_vectorstore = MagicMock()
    mock_vectorstore.similarity_search.side_effect = RuntimeError("Pinecone timeout")

    outcome = await hybrid_search_zoning_documents(
        "zoning code 55-100",
        vectorstore=mock_vectorstore,
        candidates_per_source=10,
        results=3,
        rrf_k=60,
    )

    assert outcome.dense_error == "Pinecone timeout"
    assert outcome.lexical_error is None
    assert len(outcome.fused) == 1


@pytest.mark.asyncio
async def test_hybrid_search_surfaces_a_lexical_exception_without_raising(monkeypatch, db_session):
    mock_vectorstore = MagicMock()
    mock_vectorstore.similarity_search.return_value = [_mock_doc("Some dense-only content.", "a.pdf")]

    async def _broken_search_lexical(self, query, k=5):
        raise RuntimeError("Postgres unavailable")

    monkeypatch.setattr(DocumentChunkRepository, "search_lexical", _broken_search_lexical)

    outcome = await hybrid_search_zoning_documents(
        "any query",
        vectorstore=mock_vectorstore,
        candidates_per_source=10,
        results=3,
        rrf_k=60,
    )

    assert outcome.dense_error is None
    assert outcome.lexical_error == "Postgres unavailable"
    assert len(outcome.fused) == 1
    assert outcome.fused[0].lexical_rank is None
