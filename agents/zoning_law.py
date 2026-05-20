from langchain_core.messages import SystemMessage, AIMessage, HumanMessage

from config import NodeName
from config.llm import base_model
from schema.state import OverallGraphState

# Local control toggle for this specific node
use_mock = True

def zoning_law_agent_node(state: OverallGraphState):
    """
    Zoning Law Agent: Coordinates municipal ordinance and regulatory compliance lookup.
    Granularly routes between mock records and a live compliance/LLM parsing pipeline.
    """
    # Guard Clause Check
    if use_mock:
        return _get_zoning_law_mock_response(state)

    # --- Live AI Reasoning Path ---
    target_city = state.city

    # Step 2: Query municipal rules (Usually via web search tools or local policy PDF vector stores)
    # This placeholder shows how your live agent processes complex land-use laws:
    ai_response = base_model.invoke([
        SystemMessage(
            content=(
                "You are an expert real estate attorney and municipal zoning specialist. "
                "Your job is to look up and parse local city ordinances, building codes, "
                "density caps, height restrictions, and short-term rental (STR) legal frameworks "
                "for a specified city. Provide an objective legal compliance breakdown."
            )
        ),
        HumanMessage(
            content=f"Analyze municipal codes, land-use zoning restrictions, and short-term rental rules for '{target_city}'."
        )
    ])

    # Step 3: Return the updated state payload with live regulatory insight data
    return {
        "zoning_laws": ai_response.content,
        "messages": [
            AIMessage(
                content="Zoning Law Agent: Completed live municipal registry scanning and regulatory parsing.",
                name=NodeName.ZONING_LAW_AGENT.value
            )
        ]
    }


# --- PRIVATELY SCORED MOCK PROVIDER ---
def _get_zoning_law_mock_response(state: OverallGraphState) -> dict:
    """
    Returns a static state-update payload mimicking a
    successful compliance registry tool database check.
    """
    mock_zoning_result = (
        f"Municipal Ordinance & Land-Use Registry Check for {state.city}:\n"
        "- Brickell (Zone T6-48-O / High-Density Core): Allows maximum 48 stories. Mixed-use commercial/residential overlay. "
        "Short-term rentals (STR) restricted to buildings with specific hotel licensing.\n"
        "- Wynwood (Zone NRD-1 / Neighborhood Revitalization District): 5-story height cap enforced to preserve neighborhood scale. "
        "Live-work units encouraged. STR permitted with strict transient occupancy taxes.\n"
        "Policy Notice: City council passing new transit-oriented development guidelines next quarter."
    )

    return {
        "zoning_laws": mock_zoning_result,
        "messages": [
            AIMessage(
                content="[MOCK] Zoning Law Agent: Checked municipal land-use registries. Parsed density caps and short-term rental rules.",
                name=NodeName.ZONING_LAW_AGENT.value
            )
        ]
    }