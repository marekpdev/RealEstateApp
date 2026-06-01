# file: scripts/sync_knowledge_base.py
import os
import tempfile
from dotenv import load_dotenv
from azure.storage.blob import BlobServiceClient
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore

def sync_azure_to_pinecone():
    # Load environment variables from .env file
    load_dotenv()

    # 1. Authenticate with Azure Storage Account using environment variables
    connect_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not connect_str:
        raise ValueError("Missing AZURE_STORAGE_CONNECTION_STRING in environment variables.")

    blob_service_client = BlobServiceClient.from_connection_string(connect_str)
    container_client = blob_service_client.get_container_client("zoning-laws")

    print("Scanning Azure Blob Storage for zoning PDFs...")
    blobs = container_client.list_blobs()

    all_chunks = []
    # Industry baseline text splitter for processing regulatory and legal text
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)

    # 2. Iterate through files in the Azure container
    for blob in blobs:
        if blob.name.endswith(".pdf"):
            print(f"  -> Found cloud document: {blob.name}")

            # Use a secure temp file context so no files persist on your local drive afterward
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
                temp_pdf.write(container_client.download_blob(blob).readall())
                temp_pdf_path = temp_pdf.name

            try:
                # 3. Cleanly extract and chunk the text layout
                loader = PyPDFLoader(temp_pdf_path)
                docs = loader.load()

                # Append source file metadata to help the downstream agent reference its origin
                for doc in docs:
                    doc.metadata["source_origin"] = blob.name

                chunks = text_splitter.split_documents(docs)
                all_chunks.extend(chunks)
            finally:
                # Ensure the local OS file reference is wiped immediately
                os.remove(temp_pdf_path)

    # 4. Push directly to Pinecone Cloud Server Infrastructure
    if all_chunks:
        print(f"📤 Generating embeddings and uploading {len(all_chunks)} chunks to Pinecone...")
        embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

        PineconeVectorStore.from_documents(
            documents=all_chunks,
            embedding=embeddings,
            index_name="real-estate-app"
        )
        print("✨ Database completely synchronized with Azure Cloud Storage!")
    else:
        print("🤷 No PDFs found in your Azure storage container.")


if __name__ == "__main__":
    sync_azure_to_pinecone()