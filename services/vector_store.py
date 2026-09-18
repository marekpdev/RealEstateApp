from langchain_openai import OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from config.config import PINECONE_API_KEY, PINECONE_INDEX_NAME, OPENAI_API_KEY
from config.safety import guarded_httpx_clients

def get_pinecone_vector_store():
    """
    Initializes and returns a PineconeVectorStore instance.
    """
    if not PINECONE_API_KEY:
        return None

    http_client, http_async_client = guarded_httpx_clients("services.vector_store.OpenAIEmbeddings")
    embeddings = OpenAIEmbeddings(
        api_key=OPENAI_API_KEY,
        http_client=http_client,
        http_async_client=http_async_client,
    )
    
    vectorstore = PineconeVectorStore(
        index_name=PINECONE_INDEX_NAME,
        embedding=embeddings,
        pinecone_api_key=PINECONE_API_KEY
    )
    return vectorstore
