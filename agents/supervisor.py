from langchain_core.messages import AIMessage

from config import NodeName
from schema.state import OverallGraphState

def supervisor_agent_node(state: OverallGraphState) -> dict:
    """
    Deterministic Supervisor: Acts as a structural traffic controller.
    No LLM call is required here because the routing path is static.
    """
    # Extract variables safely from the new nested object layer
    target_city = state.ingest_input.city if state.ingest_input else "Unknown Market"
    max_budget = state.ingest_input.budget if state.ingest_input else "Unknown Budget"

    return {
        "messages": [
            AIMessage(
                content=f"Supervisor: Parallel research workers initialized for market '{target_city}' with a ceiling of '{max_budget}'.",
                name=NodeName.SUPERVISOR_AGENT.value
            )
        ]
    }