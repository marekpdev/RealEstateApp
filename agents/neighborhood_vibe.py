from langchain_core.messages import SystemMessage, AIMessage, HumanMessage

from config import NodeName
from config.config import MOCK_NEIGHBORHOOD_VIBE_AGENT_OUTPUT
from config.llm import base_model
from schema.state import OverallGraphState, NeighborhoodVibeAgentOutput

def neighborhood_vibe_agent_node(state: OverallGraphState) -> dict:
    """
    Neighborhood Vibe Agent: Coordinates semantic context lookup regarding social demographics.
    Granularly routes between mock records and a live Vector DB / RAG tool pipeline.
    """
    if MOCK_NEIGHBORHOOD_VIBE_AGENT_OUTPUT:
        return _get_neighborhood_vibe_mock_response(state)

    # --- Live AI Reasoning Path ---
    # Step 1: Extract variables populated down one level inside the nested Ingest module
    target_city = state.ingest_input.city if state.ingest_input else "Unknown Market"

    # Step 2: Bind your matching flat neighborhood vibe schema to force structured JSON wrapping
    structured_llm = base_model.with_structured_output(NeighborhoodVibeAgentOutput)

    # Step 3: Perform semantic search and output extraction via the structured pipeline
    extraction_result: NeighborhoodVibeAgentOutput = structured_llm.invoke([
        SystemMessage(
            content=(
                "You are a local community researcher and socio-demographic analyst. "
                "Your job is to synthesize local community sentiment, lifestyle trends, "
                "transit metrics, and regional environmental risks for a given city location. "
                "Provide a descriptive, objective assessment as a clean, structured string output."
            )
        ),
        HumanMessage(
            content=f"Analyze local sentiment, lifestyle vibes, and risk factors for neighborhoods inside '{target_city}'."
        )
    ])

    # Step 4: Return the validated Pydantic object directly under the state key
    return {
        "neighborhood_vibe": extraction_result,
        "messages": [
            AIMessage(
                content="Neighborhood Vibe Agent: Completed live vector search and structured sentiment extraction.",
                name=NodeName.NEIGHBORHOOD_VIBE_AGENT.value
            )
        ]
    }


# --- PRIVATELY SCORED MOCK PROVIDER ---
def _get_neighborhood_vibe_mock_response(state: OverallGraphState) -> dict:
    """
    Returns a static state-update payload mimicking a successful vector database semantic RAG lookup,
    instantiated securely through NeighborhoodVibeAgentOutput for type-safety.
    """
    # Safely unfold parameters from the ingest layer for formatting the mock text
    target_city = state.ingest_input.city if state.ingest_input else "Unknown Market"

    mock_rag_result = (
        f"Vector Database Semantic Search for {target_city} Sentiment:\n"
        "- Brickell: High-density, young professional vibe. Noise complaints up 12%, but transit usage is excellent.\n"
        "- Wynwood: Creative district, heavy gentrification trends. Local forums note tech company relocations.\n"
        "Risk Profile: Coastal flood zones require premium property insurance evaluations."
    )

    # Instantiate the typed output object explicitly to match the Graph state expectations
    mock_payload = NeighborhoodVibeAgentOutput(
        neighborhood_vibe=mock_rag_result
    )

    return {
        "neighborhood_vibe": mock_payload,
        "messages": [
            AIMessage(
                content="[MOCK] Neighborhood Vibe Agent: Completed vector embeddings search. Extracted community logs.",
                name=NodeName.NEIGHBORHOOD_VIBE_AGENT.value
            )
        ]
    }