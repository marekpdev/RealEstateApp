from unittest.mock import MagicMock, patch

import pytest

from resilience.circuit_breaker import CircuitState, get_circuit_breaker
from tools.vector_tools import PINECONE_CIRCUIT_BREAKER_NAME, search_zoning_laws


def _breaker_config(failure_threshold=2, reset_timeout_seconds=999.0):
    return patch.multiple(
        "resilience.circuit_breaker.config",
        CIRCUIT_BREAKER_FAILURE_THRESHOLD=failure_threshold,
        CIRCUIT_BREAKER_RESET_TIMEOUT_SECONDS=reset_timeout_seconds,
    )


def test_search_zoning_laws_not_configured():
    with patch("tools.vector_tools.get_pinecone_vector_store", return_value=None):
        result = search_zoning_laws.invoke({"query": "Austin setback rules"})
    assert "not configured" in result


def test_search_zoning_laws_returns_formatted_results_on_success():
    mock_doc = MagicMock()
    mock_doc.metadata = {"source": "austin-zoning.pdf"}
    mock_doc.page_content = "Max height 45ft in R-3 zones."
    mock_vectorstore = MagicMock()
    mock_vectorstore.similarity_search.return_value = [mock_doc]

    with patch("tools.vector_tools.get_pinecone_vector_store", return_value=mock_vectorstore), \
         _breaker_config():
        result = search_zoning_laws.invoke({"query": "Austin height limits"})

    assert "austin-zoning.pdf" in result
    assert "Max height 45ft" in result
    assert get_circuit_breaker(PINECONE_CIRCUIT_BREAKER_NAME).state == CircuitState.CLOSED


def test_search_zoning_laws_failure_does_not_raise_and_counts_toward_breaker():
    mock_vectorstore = MagicMock()
    mock_vectorstore.similarity_search.side_effect = RuntimeError("Pinecone timeout")

    with patch("tools.vector_tools.get_pinecone_vector_store", return_value=mock_vectorstore), \
         _breaker_config(failure_threshold=2):
        result = search_zoning_laws.invoke({"query": "Austin setback rules"})

    assert "Error searching vector database" in result
    assert get_circuit_breaker(PINECONE_CIRCUIT_BREAKER_NAME).state == CircuitState.CLOSED


def test_search_zoning_laws_opens_breaker_after_threshold_and_skips_the_call():
    mock_vectorstore = MagicMock()
    mock_vectorstore.similarity_search.side_effect = RuntimeError("Pinecone timeout")

    with patch("tools.vector_tools.get_pinecone_vector_store", return_value=mock_vectorstore), \
         _breaker_config(failure_threshold=2):
        search_zoning_laws.invoke({"query": "q1"})
        search_zoning_laws.invoke({"query": "q2"})
        assert get_circuit_breaker(PINECONE_CIRCUIT_BREAKER_NAME).state == CircuitState.OPEN
        assert mock_vectorstore.similarity_search.call_count == 2

        # Breaker is open - the vector store must not be touched a third time.
        result = search_zoning_laws.invoke({"query": "q3"})
        assert mock_vectorstore.similarity_search.call_count == 2
        assert "temporarily unavailable" in result
        assert "web search" in result.lower()
