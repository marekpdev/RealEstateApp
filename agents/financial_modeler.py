from langchain_core.messages import SystemMessage, AIMessage, HumanMessage

from config import NodeName
from config.config import MOCK_FINANCIAL_MODELER_AGENT_OUTPUT
from config.llm import base_model
from schema.state import OverallGraphState, FinancialModelerAgentOutput
from utils.logger import log_agent_header, log_agent_content


async def financial_modeler_agent_node(state: OverallGraphState) -> dict:
    """
    Financial Modeler Node: Reads ALL typed worker modules, combines them via structured output,
    and granularly routes between mock outputs and a live LLM synthesis pipeline.
    """
    await log_agent_header(NodeName.FINANCIAL_MODELER_AGENT, "⚙️ Node: Financial Modeler Agent")

    if MOCK_FINANCIAL_MODELER_AGENT_OUTPUT:
        return await _get_financial_modeler_mock_response(state)

    # --- Live AI Reasoning Path ---
    # Step 1: Safely unfold variables from the nested Pydantic state modules
    target_city = state.ingest_input.city if state.ingest_input else "Unknown Market"
    # TODO need to fix it when working on financial modeler
    # market_data_ctx = state.market_data.market_data if state.market_data else "No market listing data available."
    vibe_ctx = state.neighborhood_vibe.neighborhood_vibe if state.neighborhood_vibe else "No neighborhood sentiment context available."
    zoning_ctx = state.zoning_laws.zoning_laws if state.zoning_laws else "No municipal zoning data available."

    # Step 2: Bind your matching flat financial schema to force structured JSON wrapping
    structured_llm = base_model.with_structured_output(FinancialModelerAgentOutput)

    # Step 3: Invoke the model to synthesize the final prospectus report string
    extraction_result: FinancialModelerAgentOutput = structured_llm.invoke([
        SystemMessage(
            content=(
                "You are an expert financial modeler and real estate investment strategist. "
                "Your job is to look at the collected market listings, neighborhood sentiment data, "
                "and local zoning laws to write a professional Real Estate Investment Prospectus Report. "
                "Synthesize a clear final investment verdict with cap rates and legal compliance parameters "
                "formatted cleanly in Markdown text."
            )
        ),
        HumanMessage(
            content=(
                f"Generate a comprehensive prospectus report for {target_city}.\n\n"
                f"2. COMMUNITY SENTIMENT:\n{vibe_ctx}\n\n"
                f"3. REGULATORY & ZONING LAWS:\n{zoning_ctx}\n"
            )
        )
    ])

    # Step 4: Return the validated Pydantic object directly under the state key
    return {
        "financial_report": extraction_result,
        "messages": [
            AIMessage(
                content="Financial Modeler Agent: Successfully generated the comprehensive prospectus report via structured LLM.",
                name=NodeName.FINANCIAL_MODELER_AGENT.value
            )
        ]
    }


async def _get_financial_modeler_mock_response(state: OverallGraphState) -> dict:
    """
    Returns a static state-update payload mimicking a successful multi-source synthesis analysis,
    instantiated securely through FinancialModelerAgentOutput for type-safety.
    """
    await log_agent_content(NodeName.FINANCIAL_MODELER_AGENT, "🔄 [MOCK] Financial Modeler Agent: Using mock data")

    # Safely unfold parameters for formatting the mock response string
    target_city = state.ingest_input.city if state.ingest_input else "Unknown Market"
    # TODO need to fix it when working on financial modeler
    # market_data_txt = state.market_data.market_data if state.market_data else "No mock market data."
    vibe_txt = state.neighborhood_vibe.neighborhood_vibe if state.neighborhood_vibe else "No mock vibe data."
    zoning_txt = state.zoning_laws.zoning_laws if state.zoning_laws else "No mock zoning data."

    final_report_string = (
        "### REAL ESTATE INVESTMENT PROSPECTUS REPORT ###\n\n"
        f"Analysis for Market: {target_city}\n"
        "--------------------------------------------------\n"
        f"2. COMMUNITY SENTIMENT:\n{vibe_txt}\n\n"
        f"3. REGULATORY & ZONING LAWS:\n{zoning_txt}\n\n"
        "4. FINAL INVESTMENT VERDICT:\n"
        "   - Brickell Avenue properties show a strong 6.2% cap rate, but the short-term rental restrictions "
        "     mean you must operate this strictly as a long-term leasing asset to remain legally compliant."
    )

    # Instantiate the typed output object explicitly to avoid raw string merge conflicts
    mock_payload = FinancialModelerAgentOutput(
        financial_report=final_report_string
    )

    return {
        "financial_report": mock_payload,
        "messages": [
            AIMessage(
                content="[MOCK] Financial Modeler Agent: Successfully generated the comprehensive prospectus report.",
                name=NodeName.FINANCIAL_MODELER_AGENT.value
            )
        ]
    }