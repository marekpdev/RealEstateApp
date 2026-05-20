from langchain_core.messages import SystemMessage, AIMessage, HumanMessage

from config import NodeName
from config.llm import base_model
from schema.state import OverallGraphState

# Local control toggle for this specific node
use_mock = True

def financial_modeler_agent_node(state: OverallGraphState):
    """
    Financial Modeler Node: Reads ALL three worker buckets and combines them.
    Granularly routes between mock outputs and a live LLM synthesis pipeline.
    """
    # Guard Clause Check
    if use_mock:
        return _get_financial_modeler_mock_response(state)

    # --- Live AI Reasoning Path ---
    # Step 1: Gather and format the accumulated context from the graph state
    market_data_ctx = state.market_data
    vibe_ctx = state.neighborhood_vibe
    zoning_ctx = state.zoning_laws

    # Step 2: Invoke your base model to synthesize the final prospectus report
    # (Using basic text generation here since you are generating a markdown report string)
    ai_response = base_model.invoke([
        SystemMessage(
            content=(
                "You are an expert financial modeler and real estate investment strategist. "
                "Your job is to look at the collected market listings, neighborhood sentiment data, "
                "and local zoning laws to write a professional Real Estate Investment Prospectus Report. "
                "Synthesize a clear final investment verdict with cap rates and legal compliance parameters."
            )
        ),
        HumanMessage(
            content=(
                f"Generate a comprehensive prospectus report for {state.city}.\n\n"
                f"1. MARKET DATA LISTINGS:\n{market_data_ctx}\n\n"
                f"2. COMMUNITY SENTIMENT:\n{vibe_ctx}\n\n"
                f"3. REGULATORY & ZONING LAWS:\n{zoning_ctx}\n"
            )
        )
    ])

    # Step 3: Return the updated state payload with the live report
    return {
        "financial_report": ai_response.content,
        "messages": [
            AIMessage(
                content="Financial Modeler Agent: Successfully generated the comprehensive prospectus report via LLM.",
                name=NodeName.FINANCIAL_MODELER_AGENT.value
            )
        ]
    }

def _get_financial_modeler_mock_response(state: OverallGraphState) -> dict:
    """
    Returns a static state-update payload mimicking a
    successful multi-source synthesis analysis.
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
                content="[MOCK] Financial Modeler Agent: Successfully generated the comprehensive prospectus report.",
                name=NodeName.FINANCIAL_MODELER_AGENT.value
            )
        ]
    }

