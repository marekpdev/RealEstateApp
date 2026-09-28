# file: scripts/sync_knowledge_base.py
import asyncio
import os
import tempfile
from collections import defaultdict
from typing import List

from azure.storage.blob import BlobServiceClient
from pypdf import PdfReader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config.config import MOCK_KNOWLEDGE_BASE_SYNC
from db.repositories import DocumentChunkRepository
from db.session import dispose_engine, session_scope
from resilience.circuit_breaker import get_circuit_breaker
from services.vector_store import PINECONE_CIRCUIT_BREAKER_NAME, get_pinecone_vector_store


def _chunks_to_rows(chunks: List[Document]) -> List[dict]:
    """Converts LangChain Documents into DocumentChunkRepository.replace_all()
    rows, assigning each chunk a 0-based chunk_index scoped to its own
    source_origin - the order they appear in `chunks` for a given source is
    already this document's own reading order (RecursiveCharacterTextSplitter
    preserves it), so a running per-source counter is enough; no need to sort
    on page/offset first."""
    counters: dict = defaultdict(int)
    rows = []
    for chunk in chunks:
        source_origin = chunk.metadata["source_origin"]
        rows.append(
            {
                "source_origin": source_origin,
                "page": chunk.metadata["page"],
                "chunk_index": counters[source_origin],
                "content": chunk.page_content,
            }
        )
        counters[source_origin] += 1
    return rows


def _sync_chunks_to_postgres(chunks: List[Document]) -> int:
    """Replaces the entire document_chunks table (Postgres's lexical-search
    half of retrieval) with the exact same chunks just produced for Pinecone
    - see DocumentChunkRepository.replace_all() for why a full replace,
    rather than an incremental upsert, is the correct semantics here.

    Runs its own asyncio.run() call rather than making sync_azure_to_pinecone
    itself async: this function's only two callers (this module's own
    __main__ block and worker.tasks.sync_knowledge_base, a plain non-async
    Celery task body) both call it from outside any already-running event
    loop, the same precondition worker.tasks.generate_report's own
    asyncio.run() call documents at length. dispose_engine() inside that same
    call, before the loop closes, is required for the identical reason
    (db/session.py's engine is a process-wide singleton whose pooled asyncpg
    connections are bound to whichever loop built them) - without it, a
    later task sharing this worker process (generate_report, or a second
    Beat-scheduled sync) would inherit an engine bound to this call's
    already-closed loop and fail with "Future attached to a different loop".
    """

    async def _run() -> int:
        try:
            async with session_scope() as session:
                return await DocumentChunkRepository(session).replace_all(
                    _chunks_to_rows(chunks)
                )
        finally:
            await dispose_engine()

    return asyncio.run(_run())


def _mock_sync_result() -> dict:
    """MOCK_KNOWLEDGE_BASE_SYNC's canned response - mirrors the per-agent
    MOCK_*_AGENT_OUTPUT fixtures elsewhere: deterministic, no network calls,
    so a scheduled Beat run (or a test) never touches Azure Blob Storage,
    OpenAI embeddings or Pinecone for real."""
    print("🧪 MOCK_KNOWLEDGE_BASE_SYNC is on - skipping the real Azure -> Pinecone sync.")
    return {"documents_scanned": 2, "chunks_synced": 6, "mocked": True}


def sync_azure_to_pinecone() -> dict:
    """Scans every PDF in the 'zoning-laws' Azure Blob container, chunks it,
    and upserts the chunks into Pinecone - the ingestion half of the Zoning
    Law agent's RAG pipeline (tools/vector_tools.py reads what this writes).

    Returns a summary dict (not just prints) so a caller - in particular
    worker.tasks.sync_knowledge_base, the Celery task wrapping this for
    Celery Beat - has something concrete to log, and tests have something
    to assert on.

    Gated by MOCK_KNOWLEDGE_BASE_SYNC (which OFFLINE_MODE implies), exactly
    like every agent's own MOCK_*_AGENT_OUTPUT flag gates its real call -
    this is what keeps a Beat-scheduled run from silently costing money in
    any environment where OFFLINE_MODE hasn't been explicitly turned off.
    """
    if MOCK_KNOWLEDGE_BASE_SYNC:
        return _mock_sync_result()

    # 1. Authenticate with Azure Storage Account using environment variables
    connect_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not connect_str:
        raise ValueError("Missing AZURE_STORAGE_CONNECTION_STRING in environment variables.")

    # Reuses services.vector_store's factory rather than building
    # OpenAIEmbeddings/PineconeVectorStore directly, so this real path goes
    # through the same OFFLINE_MODE-guarded httpx transport every other
    # OpenAI call in the app already does (config/safety.py).
    vectorstore = get_pinecone_vector_store()
    if not vectorstore:
        raise ValueError(
            "Pinecone is not configured - set PINECONE_API_KEY and PINECONE_INDEX_NAME."
        )

    blob_service_client = BlobServiceClient.from_connection_string(connect_str)
    container_client = blob_service_client.get_container_client("zoning-laws")

    print("🔍 Scanning Azure Blob Storage for zoning PDFs...")
    blobs = container_client.list_blobs()

    all_chunks = []
    documents_scanned = 0
    # Industry baseline text splitter for processing regulatory and legal text
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)

    # 2. Iterate through files in the Azure container
    for blob in blobs:
        if not blob.name.endswith(".pdf"):
            continue

        print(f"  -> 📄 Found cloud document: {blob.name}")
        documents_scanned += 1

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
            temp_pdf.write(container_client.download_blob(blob).readall())
            temp_pdf_path = temp_pdf.name

        try:
            # 2. Extract layout content using raw library calls
            reader = PdfReader(temp_pdf_path)
            docs = []

            for page_num, page in enumerate(reader.pages):
                text = page.extract_text()
                if text.strip():  # Skip empty structural pages
                    # Reconstruct a standard LangChain document object manually
                    docs.append(Document(
                        page_content=text,
                        metadata={"source_origin": blob.name, "page": page_num}
                    ))

            chunks = text_splitter.split_documents(docs)
            all_chunks.extend(chunks)
        finally:
            os.remove(temp_pdf_path)

    # 4. Replace Postgres's lexical-search index with these same chunks -
    # done before the Pinecone upload below, and unconditionally (even when
    # all_chunks is empty, which correctly empties the table), so a Pinecone
    # outage or an open circuit breaker never leaves the lexical half of
    # retrieval stale: the two indexes are independent failure domains, the
    # same per-upstream isolation principle the RapidAPI/Pinecone circuit
    # breakers already established elsewhere in this codebase.
    chunks_indexed_lexically = _sync_chunks_to_postgres(all_chunks)
    print(f"📚 Lexical index (Postgres) now holds {chunks_indexed_lexically} chunks.")

    # 5. Push directly to Pinecone Cloud Server Infrastructure
    if all_chunks:
        # Shares one breaker with tools/vector_tools.py's read path - both
        # sides of the same upstream (Pinecone), so a scheduled sync that's
        # failing repeatedly also fails fast instead of retrying a doomed
        # upload on every Beat tick, and vice versa.
        breaker = get_circuit_breaker(PINECONE_CIRCUIT_BREAKER_NAME)
        breaker.before_call()

        print(f"📤 Generating embeddings and uploading {len(all_chunks)} chunks to Pinecone...")
        try:
            vectorstore.add_documents(all_chunks)
        except Exception:
            breaker.record_failure()
            raise
        breaker.record_success()
        print("✨ Database completely synchronized with Azure Cloud Storage!")
    else:
        print("🤷 No PDFs found in your Azure storage container.")

    return {
        "documents_scanned": documents_scanned,
        "chunks_synced": len(all_chunks),
        "mocked": False,
    }


if __name__ == "__main__":
    sync_azure_to_pinecone()
