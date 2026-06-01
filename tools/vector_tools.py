from langchain_core.tools import tool
from services.vector_store import get_pinecone_vector_store

@tool
def search_zoning_laws(query: str) -> str:
    """
    Searches the Pinecone vector database for specific zoning laws, 
    municipal regulations, and land-use policies related to the query.
    Use this to find verified regulatory data from municipal archives.
    """
    print(f"🔍 Executing search_zoning_laws vector search for: '{query}'")

    vectorstore = get_pinecone_vector_store()
    if not vectorstore:
        return "Pinecone is not configured. Please ensure PINECONE_API_KEY and PINECONE_INDEX_NAME are set."
    
    try:
        # Perform similarity search
        results = vectorstore.similarity_search(query, k=3)
        
        if not results:
            return f"No relevant zoning laws found in the vector database for query: '{query}'."
        
        formatted_results = []
        for doc in results:
            source = doc.metadata.get('source', 'Unknown Archive')
            content = doc.page_content
            formatted_results.append(f"--- SOURCE: {source} ---\n{content}")

        print(f"✨ Found results '{formatted_results}'")

        return "\n\n".join(formatted_results)
    except Exception as e:
        return f"Error searching vector database: {str(e)}"
