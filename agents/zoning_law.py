from langchain_core.messages import SystemMessage, AIMessage, HumanMessage

from config import NodeName
from config.llm import base_model
from schema.state import OverallGraphState, ZoningLawAgentOutput

# Local control toggle for this specific node
use_mock = True

def zoning_law_agent_node(state: OverallGraphState) -> dict:
    """
    Zoning Law Agent: Coordinates municipal ordinance and regulatory compliance lookup.
    Granularly routes between mock records and a live compliance/LLM parsing pipeline.
    """
    # Guard Clause Check
    if use_mock:
        return _get_zoning_law_mock_response(state)

    # --- Live AI Reasoning Path ---
    # Step 1: Extract variables populated down one level inside the nested Ingest module
    target_city = state.ingest_input.city if state.ingest_input else "Unknown Market"

    # Step 2: Bind your matching flat zoning law schema to force structured JSON wrapping
    structured_llm = base_model.with_structured_output(ZoningLawAgentOutput)

    # Step 3: Query municipal rules via the structured model pipeline
    extraction_result: ZoningLawAgentOutput = structured_llm.invoke([
        SystemMessage(
            content=(
                "You are an expert real estate attorney and municipal zoning specialist. "
                "Your job is to look up and parse local city ordinances, building codes, "
                "density caps, height restrictions, and short-term rental (STR) legal frameworks "
                "for a specified city. Provide an objective legal compliance breakdown as a clean, "
                "structured string output."
            )
        ),
        HumanMessage(
            content=f"Analyze municipal codes, land-use zoning restrictions, and short-term rental rules for '{target_city}'."
        )
    ])

    # Step 4: Return the validated Pydantic object directly under the state key
    return {
        "zoning_laws": extraction_result,
        "messages": [
            AIMessage(
                content="Zoning Law Agent: Completed live municipal registry scanning and structured regulatory parsing.",
                name=NodeName.ZONING_LAW_AGENT.value
            )
        ]
    }


# --- PRIVATELY SCORED MOCK PROVIDER ---
def _get_zoning_law_mock_response(state: OverallGraphState) -> dict:
    """
    Returns a static state-update payload mimicking a successful compliance registry tool database check,
    instantiated securely through ZoningLawAgentOutput for type-safety.
    """
    # Safely unfold parameters from the ingest layer for formatting the mock text
    target_city = state.ingest_input.city if state.ingest_input else "Unknown Market"

    mock_zoning_result = (
        f"Municipal Ordinance & Land-Use Registry Check for {target_city}:\n"
        "- Brickell (Zone T6-48-O / High-Density Core): Allows maximum 48 stories. Mixed-use commercial/residential overlay. "
        "Short-term rentals (STR) restricted to buildings with specific hotel licensing.\n"
        "- Wynwood (Zone NRD-1 / Neighborhood Revitalization District): 5-story height cap enforced to preserve neighborhood scale. "
        "Live-work units encouraged. STR permitted with strict transient occupancy taxes.\n"
        "Policy Notice: City council passing new transit-oriented development guidelines next quarter."
    )

    # Instantiate the typed output object explicitly to match the Graph state expectations
    mock_payload = ZoningLawAgentOutput(
        zoning_laws=mock_zoning_result
    )

    return {
        "zoning_laws": mock_payload,
        "messages": [
            AIMessage(
                content="[MOCK] Zoning Law Agent: Checked municipal land-use registries. Parsed density caps and short-term rental rules.",
                name=NodeName.ZONING_LAW_AGENT.value
            )
        ]
    }