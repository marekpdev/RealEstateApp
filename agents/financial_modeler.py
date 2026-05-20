from langchain_core.messages import AIMessage

from schema.state import OverallGraphState
from config import NodeName

def mock_financial_modeler_node(state: OverallGraphState):
    """
    Mock Synthesizer Node: Reads ALL three worker buckets and combines them.
    """
    final_report = (
        "### REAL ESTATE INVESTMENT PROSPECTUS REPORT ###\n\n"
        f"Analysis for Market: {state.city}\n"
        "--------------------------------------------------\n"
        f"1. MARKET DATA LISTINGS:\n{state.market_data}\n\n"
        f"2. COMMUNITY SENTIMENT:\n{state.neighborhood_vibe}\n\n"
        f"3. REGULATORY & ZONING LAWS:\n{state.zoning_laws}\n\n"
        "4. FINAL INVESTMENT VERDICT:\n"
        "   - Brickell Avenue properties show a strong 6.2% cap rate, but the short-term rental restrictions "
        "     mean you must operate this strictly as a long-term leasing asset to remain legally compliant."
    )

    # print(f"Generated financial report for {state.city}: {final_report}")

    return {
        "financial_report": final_report,
        "messages": [
            AIMessage(
                content="Financial Modeler Agent: Successfully generated the comprehensive prospectus report.",
                name=NodeName.FINANCIAL_MODELER_AGENT.value
            )
        ]
    }