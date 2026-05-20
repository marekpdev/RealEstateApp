from langchain_core.messages import AIMessage

from state import OverallGraphState

def mock_financial_modeler_node(state: OverallGraphState):
    """
    Mock Synthesizer Node: Reads the compiled text variables written by both
    research nodes and produces the final investment report.
    """
    # Access both data buckets populated by your parallel workers
    listings_context = state.market_data
    vibe_context = state.neighborhood_vibe

    final_report = (
        "### REAL ESTATE INVESTMENT PROSPECTUS REPORT ###\n\n"
        f"Based on historical metrics ({listings_context}) and qualitative social indicators ({vibe_context}), "
        f"the recommendation is a BUY for Brickell Ave due to superior cap rate margins, provided safety contingencies "
        "for coastal flood pricing updates are calculated into the escrow layout."
    )

    return {
        "messages": [
            AIMessage(
                content=final_report,
                name="FinancialModeler"
            )
        ]
    }