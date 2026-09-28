from unittest.mock import MagicMock, patch

import pytest

from db.repositories.document_chunk_repository import DocumentChunkRepository
from resilience.circuit_breaker import CircuitState, get_circuit_breaker
from tools.hybrid_retrieval_tools import PINECONE_CIRCUIT_BREAKER_NAME, search_zoning_laws_hybrid


def _breaker_config(failure_threshold=2, reset_timeout_seconds=999.0):
    return patch.multiple(
        "resilience.circuit_breaker.config",
        CIRCUIT_BREAKER_FAILURE_THRESHOLD=failure_threshold,
        CIRCUIT_BREAKER_RESET_TIMEOUT_SECONDS=reset_timeout_seconds,
    )


def _mock_doc(content: str, source: str) -> MagicMock:
    doc = MagicMock()
    doc.page_content = content
    doc.metadata = {"source": source}
    return doc


@pytest.mark.asyncio
async def test_hybrid_tool_not_configured_degrades_to_lexical_only(db_session):
    await DocumentChunkRepository(db_session).replace_all(
        [{"source_origin": "a.pdf", "page": 1, "chunk_index": 0, "content": "Ordinance 12-345 governs setbacks."}]
    )
    await db_session.commit()

    with patch("tools.hybrid_retrieval_tools.get_pinecone_vector_store", return_value=None), _breaker_config():
        result = await search_zoning_laws_hybrid.ainvoke({"query": "12-345"})

    assert "not configured" in result
    assert "showing lexical-only results" in result
    assert "a.pdf" in result
    assert get_circuit_breaker(PINECONE_CIRCUIT_BREAKER_NAME).state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_hybrid_tool_fuses_dense_and_lexical_results(db_session):
    await DocumentChunkRepository(db_session).replace_all(
        [{"source_origin": "lexical.pdf", "page": 1, "chunk_index": 0, "content": "Setback rules for R-3 zones downtown."}]
    )
    await db_session.commit()

    mock_vectorstore = MagicMock()
    mock_vectorstore.similarity_search.return_value = [_mock_doc("Max height 45ft in R-3 zones.", "dense.pdf")]

    with patch("tools.hybrid_retrieval_tools.get_pinecone_vector_store", return_value=mock_vectorstore), _breaker_config():
        result = await search_zoning_laws_hybrid.ainvoke({"query": "setback R-3"})

    assert "dense.pdf" in result
    assert "lexical.pdf" in result
    assert "rrf_score=" in result
    assert get_circuit_breaker(PINECONE_CIRCUIT_BREAKER_NAME).state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_hybrid_tool_records_dense_failure_and_still_returns_lexical_results(db_session):
    await DocumentChunkRepository(db_session).replace_all(
        [{"source_origin": "lexical.pdf", "page": 1, "chunk_index": 0, "content": "Setback rules for R-3 zones downtown."}]
    )
    await db_session.commit()

    mock_vectorstore = MagicMock()
    mock_vectorstore.similarity_search.side_effect = RuntimeError("Pinecone timeout")

    with patch("tools.hybrid_retrieval_tools.get_pinecone_vector_store", return_value=mock_vectorstore), \
         _breaker_config(failure_threshold=2):
        result = await search_zoning_laws_hybrid.ainvoke({"query": "setback"})

    assert "lexical.pdf" in result
    assert "dense/Pinecone search unavailable" in result
    assert get_circuit_breaker(PINECONE_CIRCUIT_BREAKER_NAME).state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_hybrid_tool_opens_breaker_after_threshold_and_skips_the_dense_call(db_session):
    await DocumentChunkRepository(db_session).replace_all(
        [{"source_origin": "lexical.pdf", "page": 1, "chunk_index": 0, "content": "Setback rules for R-3 zones downtown."}]
    )
    await db_session.commit()

    mock_vectorstore = MagicMock()
    mock_vectorstore.similarity_search.side_effect = RuntimeError("Pinecone timeout")

    with patch("tools.hybrid_retrieval_tools.get_pinecone_vector_store", return_value=mock_vectorstore), \
         _breaker_config(failure_threshold=2):
        await search_zoning_laws_hybrid.ainvoke({"query": "setback"})
        await search_zoning_laws_hybrid.ainvoke({"query": "setback"})
        assert get_circuit_breaker(PINECONE_CIRCUIT_BREAKER_NAME).state == CircuitState.OPEN
        assert mock_vectorstore.similarity_search.call_count == 2

        result = await search_zoning_laws_hybrid.ainvoke({"query": "setback"})
        assert mock_vectorstore.similarity_search.call_count == 2
        assert "circuit breaker" in result
        assert "lexical.pdf" in result


@pytest.mark.asyncio
async def test_hybrid_tool_returns_no_results_message_when_both_sides_are_empty(db_session):
    await DocumentChunkRepository(db_session).replace_all([])
    await db_session.commit()

    mock_vectorstore = MagicMock()
    mock_vectorstore.similarity_search.return_value = []

    with patch("tools.hybrid_retrieval_tools.get_pinecone_vector_store", return_value=mock_vectorstore), _breaker_config():
        result = await search_zoning_laws_hybrid.ainvoke({"query": "nonexistent-term-xyz"})

    assert "No relevant zoning laws found" in result
