from langchain_core.tools import tool
from resilience.circuit_breaker import CircuitBreakerOpenError, get_circuit_breaker
from services.vector_store import PINECONE_CIRCUIT_BREAKER_NAME, get_pinecone_vector_store

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

    breaker = get_circuit_breaker(PINECONE_CIRCUIT_BREAKER_NAME)
    try:
        breaker.before_call()
    except CircuitBreakerOpenError as exc:
        print(f"⚡ {exc}")
        # A defined degraded response, not a raised exception: this tool
        # already returns error strings rather than raising (see the except
        # branch below), and the calling agent's own system prompt already
        # treats "search_zoning_laws returns no results or insufficient
        # data" as the signal to fall back to a web search instead - so a
        # clearly-worded degraded string here plugs straight into a
        # fallback path that already exists.
        return (
            "Zoning law vector search is temporarily unavailable: a circuit "
            f"breaker opened after repeated Pinecone failures and won't let "
            f"another call through for about {exc.retry_after_seconds:.0f}s. "
            "Use the web search tool for this query instead."
        )

    try:
        results = vectorstore.similarity_search(query, k=3)
    except Exception as e:
        breaker.record_failure()
        return f"Error searching vector database: {str(e)}"

    breaker.record_success()

    if not results:
        return f"No relevant zoning laws found in the vector database for query: '{query}'."

    formatted_results = []
    for doc in results:
        source = doc.metadata.get('source', 'Unknown Archive')
        content = doc.page_content
        formatted_results.append(f"--- SOURCE: {source} ---\n{content}")

    print(f"✨ Found results '{formatted_results}'")

    return "\n\n".join(formatted_results)
