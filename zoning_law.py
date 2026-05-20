from langchain_core.messages import AIMessage

from state import OverallGraphState

def mock_zoning_law_node(state: OverallGraphState):
    """
    Mock Zoning Law Agent: Simulates querying local city ordinances and municipal codes
    using the extracted state variables to verify land use constraints.
    """
    # Simulating a regulatory database check based on state.city
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
                content="Zoning Law Agent: Checked municipal land-use registries. Parsed density caps and short-term rental rules.",
                name="ZoningAgent"
            )
        ]
    }