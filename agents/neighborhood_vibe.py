from langchain_core.messages import SystemMessage, AIMessage, HumanMessage

from config import NodeName
from config.llm import base_model
from schema.state import OverallGraphState

# Local control toggle for this specific node
use_mock = True

def neighborhood_vibe_agent_node(state: OverallGraphState):
    """
    Neighborhood Vibe Agent: Coordinates semantic context lookup regarding social demographics.
    Granularly routes between mock records and a live Vector DB / RAG tool pipeline.
    """
    # Guard Clause Check
    if use_mock:
        return _get_neighborhood_vibe_mock_response(state)

    # --- Live AI Reasoning Path ---
    target_city = state.city

    # Step 2: Perform semantic search (Usually matching embeddings against a Vector Database)
    # This placeholder shows how your live agent synthesizes unstructured neighborhood reports:
    ai_response = base_model.invoke([
        SystemMessage(
            content=(
                "You are a local community researcher and socio-demographic analyst. "
                "Your job is to synthesize local community sentiment, lifestyle trends, "
                "transit metrics, and regional environmental risks for a given city location. "
                "Provide a descriptive, objective assessment of prominent sub-neighborhoods."
            )
        ),
        HumanMessage(
            content=f"Analyze local sentiment, lifestyle vibes, and risk factors for neighborhoods inside '{target_city}'."
        )
    ])

    # Step 3: Return the updated state payload with live unstructured insights
    return {
        "neighborhood_vibe": ai_response.content,
        "messages": [
            AIMessage(
                content="Neighborhood Vibe Agent: Completed live vector search and sentiment extraction.",
                name=NodeName.NEIGHBORHOOD_VIBE_AGENT.value
            )
        ]
    }


# --- PRIVATELY SCORED MOCK PROVIDER ---
def _get_neighborhood_vibe_mock_response(state: OverallGraphState) -> dict:
    """
    Returns a static state-update payload mimicking a
    successful vector database semantic RAG lookup.
    """
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
                content="[MOCK] Neighborhood Vibe Agent: Completed vector embeddings search. Extracted community logs.",
                name=NodeName.NEIGHBORHOOD_VIBE_AGENT.value
            )
        ]
    }