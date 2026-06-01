from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from langchain.agents import create_agent
from langchain_core.runnables import RunnableConfig

from config import NodeName, config
from config.config import MOCK_FINANCIAL_MODELER_AGENT_OUTPUT
from config.llm import base_model
from schema.state import OverallGraphState, FinancialModelerAgentOutput
from tools import repl_tool
from utils.logger import log_agent_header, log_agent_content
from utils.utils import print_model, load_mock_fixture
from utils.callbacks import ToolLoggingCallbackHandler

_COMPILED_FINANCIAL_MODELER_AGENT = None


async def _get_compiled_financial_modeler_agent():
    """
    Lazy-loads and caches the financial modeler agent instance.
    """
    global _COMPILED_FINANCIAL_MODELER_AGENT
    if _COMPILED_FINANCIAL_MODELER_AGENT is not None:
        return _COMPILED_FINANCIAL_MODELER_AGENT

    system_instructions = (
        "You are an expert real estate financial underwriting model. Your job is to take raw inputs, "
        "use your python_repl tool to calculate key real estate metrics (Cap Rate, cash-on-cash, etc.), "
        "and then render an elegant, presentation-ready real estate investment memo.\n\n"
        
        "📊 CHAINLIT VISUAL FORMATTING REQUIREMENT:\n"
        "You must output your response using advanced, scannable Markdown and raw HTML elements to create a UI look. "
        "Use the following structural tools:\n"
        "- Use clear hierarchy with Markdown headings (##, ###).\n"
        "- Use horizontal rules (---) to separate sections cleanly.\n"
        "- Create 'Visual Metric Cards' using blockquotes and bold metrics (e.g., '> **💰 CAP RATE:** `6.8%`').\n"
        "- Organize the core numbers into a beautifully formatted Markdown Table.\n"
        "- Use bold text judiciously to guide the reader's eye to risk multipliers.\n"
    )

    _COMPILED_FINANCIAL_MODELER_AGENT = create_agent(
        model=base_model,
        tools=[repl_tool],
        system_prompt=system_instructions,
        # The agent natively handles the validation loop on exit and saves 
        # the verified Pydantic model directly into the 'structured_response' key.
        response_format=FinancialModelerAgentOutput,
    )
    return _COMPILED_FINANCIAL_MODELER_AGENT


async def financial_modeler_agent_node(state: OverallGraphState) -> dict:
    """
    Financial Modeler Node: Reads ALL typed worker modules, combines them via structured output,
    and executes its own math via Python to generate a beautiful raw Markdown dashboard.
    """
    await log_agent_header(NodeName.FINANCIAL_MODELER_AGENT, "⚙️ Node: Financial Modeler Agent")

    if MOCK_FINANCIAL_MODELER_AGENT_OUTPUT:
        return await _get_financial_modeler_mock_response()

    # 1. Fetch the globally cached agent harness
    agent = await _get_compiled_financial_modeler_agent()

    # 2. Safely unfold variables from the nested Pydantic state modules
    target_city = state.ingest_input.city if state.ingest_input else "Unknown Market"
    market_str = str(state.market_data) if state.market_data else "No market listing data available."
    vibe_str = state.neighborhood_vibe.neighborhood_vibe if state.neighborhood_vibe else "No neighborhood sentiment context available."
    zoning_str = state.zoning_laws.zoning_laws if state.zoning_laws else "No municipal zoning data available."

    user_message = (
        f"Generate the final investment report for {target_city} based on these three raw datasets:\n\n"
        f"### MARKET DATA:\n{market_str}\n\n"
        f"### NEIGHBORHOOD VIBE:\n{vibe_str}\n\n"
        f"### ZONING LAWS:\n{zoning_str}\n"
    )

    # 3. Construct a clean starting input state for this agent run.
    agent_input = {
        "messages": [
            HumanMessage(content=user_message)
        ]
    }

    await log_agent_content(NodeName.FINANCIAL_MODELER_AGENT, "🤖 Synthesizing report & calculating metrics via Python REPL...")

    # 4. Invoke the worker agent with our starting context package
    agent_config: RunnableConfig = {
        "callbacks": [ToolLoggingCallbackHandler(NodeName.FINANCIAL_MODELER_AGENT)],
        "recursion_limit": 10
    }

    agent_output = await agent.ainvoke(
        agent_input,
        config=agent_config
    )

    await log_agent_content(NodeName.FINANCIAL_MODELER_AGENT, "✅ Structured report generation complete. Updating graph state.")

    # 5. Extract the validated Pydantic object from the standardized output channel
    extraction_result: FinancialModelerAgentOutput = agent_output["structured_response"]

    print_model(extraction_result)

    await log_agent_content(NodeName.FINANCIAL_MODELER_AGENT, extraction_result.financial_report)

    return {
        "financial_report": extraction_result,
        "messages": [
            AIMessage(
                content="Financial Modeler Agent: Successfully generated the comprehensive prospectus report via Python-assisted reasoning.",
                name=NodeName.FINANCIAL_MODELER_AGENT.value
            )
        ]
    }

# --- PRIVATELY SCORED MOCK PROVIDER ---
async def _get_financial_modeler_mock_response() -> dict:
    """
    Returns a static state-update payload mimicking a successful multi-source synthesis analysis,
    instantiated securely through FinancialModelerAgentOutput for type-safety.
    """
    await log_agent_content(NodeName.FINANCIAL_MODELER_AGENT, "🔄 [MOCK] Financial Modeler Agent: Using mock data")

    mock_payload = load_mock_fixture("mock_financial_modeler_output_payload.json", FinancialModelerAgentOutput)

    await log_agent_content(NodeName.FINANCIAL_MODELER_AGENT, mock_payload.financial_report)

    return {
        "financial_report": mock_payload,
        "messages": [
            AIMessage(
                content="[MOCK] Financial Modeler Agent: Successfully generated the comprehensive prospectus report.",
                name=NodeName.FINANCIAL_MODELER_AGENT.value
            )
        ]
    }