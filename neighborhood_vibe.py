from langchain_core.messages import AIMessage

from state import OverallGraphState

def mock_neighborhood_vibe_node(state: OverallGraphState):
    """
    Mock Neighborhood Vibe Agent: Simulates executing a RAG vector lookup
    across unstructured community documentation (Azure AI Search / Reddit MCP).
    """
    # Simulating semantic search embeddings retrieval matching the city context
    mock_rag_result = (
        f"Vector Database Semantic Search for {state.city} Sentiment:\n"
        "- Brickell: High-density, young professional vibe. Noise complaints up 12%, but transit usage is excellent.\n"
        "- Wynwood: Creative district, heavy gentrification trends. Local forums note tech company relocations.\n"
        "Risk Profile: Coastal flood zones require premium property insurance evaluations."
    )

    return {
        "neighborhood_vibe": mock_rag_result,
        "messages": [
            AIMessage(
                content="Neighborhood Vibe Agent: Completed vector embeddings search. Extracted community logs.",
                name="VibeAgent"
            )
        ]
    }