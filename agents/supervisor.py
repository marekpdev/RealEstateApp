from langchain_core.messages import AIMessage

from config import NodeName
from schema.state import OverallGraphState
from utils.logger import log_agent_header, log_agent_content, log_agent_footer


async def supervisor_agent_node(state: OverallGraphState) -> dict:
    """
    Supervisor Agent: Acts as a structural traffic controller.
    No LLM call is required here because the routing path is static.
    """
    await log_agent_header(NodeName.SUPERVISOR_AGENT, "⚙️ Node: Supervisor Agent")

    # Extract variables safely from the new nested object layer
    target_city = state.ingest_input.city if state.ingest_input else "Unknown Market"
    max_budget = state.ingest_input.budget if state.ingest_input else "Unknown Budget"

    await log_agent_content(NodeName.SUPERVISOR_AGENT, f"🔄 Supervisor Agent: Using data: city '{target_city}' and max budget '{max_budget}'.")

    await log_agent_footer(NodeName.SUPERVISOR_AGENT)

    return {
        "messages": [
            AIMessage(
                content=f"Supervisor Agent: Parallel research workers initialized for market '{target_city}' with a ceiling of '{max_budget}'.",
                name=NodeName.SUPERVISOR_AGENT.value
            )
        ]
    }