import os
from langchain_openai import OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from config.config import PINECONE_API_KEY, PINECONE_INDEX_NAME, OPENAI_API_KEY

def get_pinecone_vector_store():
    """
    Initializes and returns a PineconeVectorStore instance.
    """
    if not PINECONE_API_KEY:
        return None
    
    # Initialize embeddings. Using OpenAIEmbeddings with the provided key.
    # Note: If GITHUB_MODELS supports embeddings, we could configure it here similarly to ChatOpenAI.
    embeddings = OpenAIEmbeddings(api_key=OPENAI_API_KEY)
    
    vectorstore = PineconeVectorStore(
        index_name=PINECONE_INDEX_NAME,
        embedding=embeddings,
        pinecone_api_key=PINECONE_API_KEY
    )
    return vectorstore
