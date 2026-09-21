from unittest.mock import MagicMock, patch

import pytest

from scripts.sync_knowledge_base import sync_azure_to_pinecone


def test_mock_flag_skips_real_sync_entirely():
    """MOCK_KNOWLEDGE_BASE_SYNC short-circuits before any Azure/OpenAI/
    Pinecone call - proven by not patching BlobServiceClient, PdfReader or
    get_pinecone_vector_store out at all. If the mock branch weren't taken
    first, this would instead fail on the missing AZURE_STORAGE_CONNECTION_STRING
    check (or a real network call, if credentials happened to be present)."""
    with patch("scripts.sync_knowledge_base.MOCK_KNOWLEDGE_BASE_SYNC", True):
        result = sync_azure_to_pinecone()

    assert result == {"documents_scanned": 2, "chunks_synced": 6, "mocked": True}


def test_real_path_requires_azure_connection_string():
    with (
        patch("scripts.sync_knowledge_base.MOCK_KNOWLEDGE_BASE_SYNC", False),
        patch.dict("os.environ", {"AZURE_STORAGE_CONNECTION_STRING": ""}),
    ):
        with pytest.raises(ValueError, match="AZURE_STORAGE_CONNECTION_STRING"):
            sync_azure_to_pinecone()


def test_real_path_requires_pinecone_configured():
    """get_pinecone_vector_store() returns None when PINECONE_API_KEY isn't
    set (services/vector_store.py's own contract) - this must surface as a
    clear error, not an AttributeError from calling .add_documents() on None."""
    with (
        patch("scripts.sync_knowledge_base.MOCK_KNOWLEDGE_BASE_SYNC", False),
        patch.dict("os.environ", {"AZURE_STORAGE_CONNECTION_STRING": "fake-conn-str"}),
        patch("scripts.sync_knowledge_base.get_pinecone_vector_store", return_value=None),
    ):
        with pytest.raises(ValueError, match="Pinecone is not configured"):
            sync_azure_to_pinecone()


def test_real_path_scans_blobs_chunks_and_upserts_into_pinecone():
    """Every external call (Azure Blob, PDF parsing, Pinecone) is mocked out;
    this proves the script's own control flow - one blob in, real chunks
    produced from its extracted text, all of them hitting add_documents()
    on the same vector store get_pinecone_vector_store() returned - not any
    real vendor's behavior."""
    mock_vectorstore = MagicMock()

    mock_page = MagicMock()
    mock_page.extract_text.return_value = "Zoning ordinance 12-345 text. " * 100
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page]

    mock_blob = MagicMock()
    mock_blob.name = "austin-zoning.pdf"
    mock_container_client = MagicMock()
    mock_container_client.list_blobs.return_value = [mock_blob]
    mock_container_client.download_blob.return_value.readall.return_value = b"%PDF-fake"
    mock_blob_service_client = MagicMock()
    mock_blob_service_client.get_container_client.return_value = mock_container_client

    with (
        patch("scripts.sync_knowledge_base.MOCK_KNOWLEDGE_BASE_SYNC", False),
        patch.dict("os.environ", {"AZURE_STORAGE_CONNECTION_STRING": "fake-conn-str"}),
        patch(
            "scripts.sync_knowledge_base.BlobServiceClient.from_connection_string",
            return_value=mock_blob_service_client,
        ),
        patch("scripts.sync_knowledge_base.PdfReader", return_value=mock_reader),
        patch(
            "scripts.sync_knowledge_base.get_pinecone_vector_store",
            return_value=mock_vectorstore,
        ),
    ):
        result = sync_azure_to_pinecone()

    mock_vectorstore.add_documents.assert_called_once()
    uploaded_chunks = mock_vectorstore.add_documents.call_args[0][0]
    assert len(uploaded_chunks) > 0
    assert uploaded_chunks[0].metadata["source_origin"] == "austin-zoning.pdf"
    assert result == {
        "documents_scanned": 1,
        "chunks_synced": len(uploaded_chunks),
        "mocked": False,
    }


def test_real_path_ignores_non_pdf_blobs_and_skips_upload_when_nothing_found():
    mock_blob = MagicMock()
    mock_blob.name = "readme.txt"
    mock_container_client = MagicMock()
    mock_container_client.list_blobs.return_value = [mock_blob]
    mock_blob_service_client = MagicMock()
    mock_blob_service_client.get_container_client.return_value = mock_container_client

    mock_vectorstore = MagicMock()

    with (
        patch("scripts.sync_knowledge_base.MOCK_KNOWLEDGE_BASE_SYNC", False),
        patch.dict("os.environ", {"AZURE_STORAGE_CONNECTION_STRING": "fake-conn-str"}),
        patch(
            "scripts.sync_knowledge_base.BlobServiceClient.from_connection_string",
            return_value=mock_blob_service_client,
        ),
        patch(
            "scripts.sync_knowledge_base.get_pinecone_vector_store",
            return_value=mock_vectorstore,
        ),
    ):
        result = sync_azure_to_pinecone()

    mock_vectorstore.add_documents.assert_not_called()
    assert result == {"documents_scanned": 0, "chunks_synced": 0, "mocked": False}
