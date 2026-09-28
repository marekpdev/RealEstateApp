import asyncio
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import delete, select

from db.models import DocumentChunk
from db.repositories import DocumentChunkRepository
from db.session import dispose_engine, session_scope
from resilience.circuit_breaker import CircuitBreakerOpenError, CircuitState, get_circuit_breaker
from scripts.sync_knowledge_base import sync_azure_to_pinecone
from services.vector_store import PINECONE_CIRCUIT_BREAKER_NAME


async def _clear_document_chunks() -> None:
    async with session_scope() as session:
        await session.execute(delete(DocumentChunk))
    await dispose_engine()


async def _read_all_document_chunks():
    async with session_scope() as session:
        rows = (await session.execute(select(DocumentChunk))).scalars().all()
    await dispose_engine()
    return rows


async def _seed_one_unrelated_chunk() -> None:
    async with session_scope() as session:
        await DocumentChunkRepository(session).replace_all(
            [{
                "source_origin": "stale-doc.pdf",
                "page": 0,
                "chunk_index": 0,
                "content": "This chunk predates the current sync run.",
            }]
        )
    await dispose_engine()


@pytest.fixture(autouse=True)
def _clean_document_chunks_table():
    """document_chunks is written directly against the real (non-isolated)
    default engine by sync_azure_to_pinecone() itself - the same "real DB,
    manual cleanup" pattern tests/test_worker.py already established for
    worker.tasks functions that drive their own asyncio.run() call (see that
    file's _create_real_request_row() docstring). Every test in this module
    fully replaces the table's contents anyway (DocumentChunkRepository.
    replace_all()'s whole point), so clearing before and after just keeps
    one test's leftover rows from making a later test's assertions ambiguous."""
    asyncio.run(_clear_document_chunks())
    yield
    asyncio.run(_clear_document_chunks())


def test_mock_flag_skips_real_sync_entirely():
    """MOCK_KNOWLEDGE_BASE_SYNC short-circuits before any Azure/OpenAI/
    Pinecone call - proven by not patching BlobServiceClient, PdfReader or
    get_pinecone_vector_store out at all. If the mock branch weren't taken
    first, this would instead fail on the missing AZURE_STORAGE_CONNECTION_STRING
    check (or a real network call, if credentials happened to be present)."""
    with (
        patch("scripts.sync_knowledge_base.MOCK_KNOWLEDGE_BASE_SYNC", True),
        patch("scripts.sync_knowledge_base._sync_chunks_to_postgres") as mock_sync_pg,
    ):
        result = sync_azure_to_pinecone()

    mock_sync_pg.assert_not_called()
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


def _single_pdf_blob_setup():
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
    return mock_blob_service_client, mock_reader


def test_a_failed_upload_counts_toward_the_shared_pinecone_breaker():
    """add_documents() shares one breaker (services/vector_store.py's
    PINECONE_CIRCUIT_BREAKER_NAME) with tools/vector_tools.py's read path -
    a failed upload here must be recorded against that same breaker, and
    the original exception must still propagate (Celery's own task-failure
    handling is what's expected to notice and log this, not this script)."""
    mock_blob_service_client, mock_reader = _single_pdf_blob_setup()
    mock_vectorstore = MagicMock()
    mock_vectorstore.add_documents.side_effect = RuntimeError("Pinecone upsert failed")

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
        patch.multiple(
            "resilience.circuit_breaker.config",
            CIRCUIT_BREAKER_FAILURE_THRESHOLD=1,
            CIRCUIT_BREAKER_RESET_TIMEOUT_SECONDS=999.0,
        ),
    ):
        with pytest.raises(RuntimeError, match="Pinecone upsert failed"):
            sync_azure_to_pinecone()

        assert get_circuit_breaker(PINECONE_CIRCUIT_BREAKER_NAME).state == CircuitState.OPEN


def test_sync_fails_fast_without_uploading_once_the_breaker_is_open():
    mock_blob_service_client, mock_reader = _single_pdf_blob_setup()
    mock_vectorstore = MagicMock()

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
        patch.multiple(
            "resilience.circuit_breaker.config",
            CIRCUIT_BREAKER_FAILURE_THRESHOLD=1,
            CIRCUIT_BREAKER_RESET_TIMEOUT_SECONDS=999.0,
        ),
    ):
        get_circuit_breaker(PINECONE_CIRCUIT_BREAKER_NAME).record_failure()

        with pytest.raises(CircuitBreakerOpenError):
            sync_azure_to_pinecone()

        mock_vectorstore.add_documents.assert_not_called()

    # The lexical (Postgres) index is written before the Pinecone breaker
    # check, deliberately - an open Pinecone breaker must never leave
    # search_lexical() stale too, since the two are independent failure
    # domains (see sync_azure_to_pinecone()'s own comment on this ordering).
    rows = asyncio.run(_read_all_document_chunks())
    assert len(rows) > 0
    assert all(row.source_origin == "austin-zoning.pdf" for row in rows)


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

    # An empty Azure container must also empty the lexical index - a
    # scheduled resync always means "this is everything currently in Azure"
    # (worker.tasks.sync_knowledge_base's own docstring), so zero PDFs found
    # must leave zero rows in document_chunks, not whatever was there before.
    rows = asyncio.run(_read_all_document_chunks())
    assert rows == []


def test_real_path_persists_the_same_chunks_to_the_postgres_lexical_index():
    """The lexical (Postgres) index must hold the exact same chunks - same
    source_origin/page/content - as the ones just produced for Pinecone, not
    a separately re-chunked copy: a future hybrid retriever combining both
    can only be compared meaningfully if both retrieval paths see identical
    text units (see db/models.py's DocumentChunk docstring)."""
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

    mock_vectorstore = MagicMock()

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

    uploaded_chunks = mock_vectorstore.add_documents.call_args[0][0]
    rows = asyncio.run(_read_all_document_chunks())

    assert result["chunks_synced"] == len(rows) == len(uploaded_chunks)
    assert [row.chunk_index for row in sorted(rows, key=lambda r: r.chunk_index)] == list(
        range(len(rows))
    )
    for row, chunk in zip(sorted(rows, key=lambda r: r.chunk_index), uploaded_chunks):
        assert row.source_origin == chunk.metadata["source_origin"] == "austin-zoning.pdf"
        assert row.page == chunk.metadata["page"]
        assert row.content == chunk.page_content


def test_real_path_replaces_stale_chunks_from_a_previous_sync():
    """A resync must wipe whatever a previous run left behind, not append to
    it - otherwise a document removed from Azure since the last sync would
    linger in lexical search forever (see DocumentChunkRepository.replace_all's
    own docstring for why an incremental upsert isn't possible here)."""
    asyncio.run(_seed_one_unrelated_chunk())

    mock_page = MagicMock()
    mock_page.extract_text.return_value = "Fresh zoning text. " * 50
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page]

    mock_blob = MagicMock()
    mock_blob.name = "dallas-zoning.pdf"
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
            return_value=MagicMock(),
        ),
    ):
        sync_azure_to_pinecone()

    rows = asyncio.run(_read_all_document_chunks())
    assert all(row.source_origin != "stale-doc.pdf" for row in rows)
    assert all(row.source_origin == "dallas-zoning.pdf" for row in rows)
