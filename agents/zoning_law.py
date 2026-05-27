from langchain_core.messages import SystemMessage, AIMessage, HumanMessage

from config import NodeName, config
from config.config import MOCK_ZONING_LAW_AGENT_OUTPUT
from config.llm import base_model
from schema.state import OverallGraphState, ZoningLawAgentOutput
from tools.tools import UnifiedMCPGateway
from utils.logger import log_agent_header, log_agent_content

async def zoning_law_agent_node(state: OverallGraphState) -> dict:
    """
        Zoning Law Agent: Executes an iterative ReAct research cycle over MCP tools.
        Once tools are exhausted, it builds a finalized Pydantic structured output.
    """
    await log_agent_header(NodeName.ZONING_LAW_AGENT, "⚙️ Node: Zoning Law Agent")

    if MOCK_ZONING_LAW_AGENT_OUTPUT:
        return await _get_zoning_law_mock_response(state)

    target_city = state.ingest_input.city if state.ingest_input else "Unknown Market"

    # 1. STATE PERSISTENCE: Check if we are starting fresh or reading a tool loop return
    # TODO need to fix it
    messages = list(state.messages)
    if not messages:
        await log_agent_content(NodeName.ZONING_LAW_AGENT, "🌱 Context empty. Initializing zoning search objectives...")
        messages = [
            SystemMessage(
                content=(
                    f"You are an expert real estate compliance agent. Discover local multi-family "
                    f"residential zoning rules and accessory dwelling unit (ADU) laws for {target_city} in 2026.\n\n"
                    f"CRITICAL WORKFLOW:\n"
                    f"1. Use 'brave_search' to find recent zoning articles.\n"
                    f"2. If a snippet cuts off or references a major ordinance, pass its URL into 'fetch_service' to read the full text.\n"
                    f"3. Gather verifiable density facts before finishing."
                )
            ),
            HumanMessage(
                content=f"Perform a comprehensive zoning search for multi-family property upgrades in {target_city}.")
        ]

    tools_list = await UnifiedMCPGateway.get_tools(
        allowed_tool_names=["brave_search", "fetch_service"],
    )
    llm_with_tools = base_model.bind(tools=tools_list)

    await log_agent_content(NodeName.ZONING_LAW_AGENT, "🤖 Analyzing historical log and determining next action...")

    response = await llm_with_tools.ainvoke(messages)

    if response.tool_calls:
        return {
            "messages": [response]
        }

    await log_agent_content(NodeName.ZONING_LAW_AGENT, "✨ Research complete. Compiling structured legal summary...")

    structured_llm = base_model.with_structured_output(ZoningLawAgentOutput)

    extraction_prompt = [
        SystemMessage(
            content=(
                "You are an expert real estate attorney. Look over the provided research log history. "
                "Extract and parse local ordinances, building codes, density caps, and height restrictions "
                "into the required structured formatting schema."
            )
        )
        ] + messages + [response]  # Inject all collected search and page text records directly into the parser

    extraction_result: ZoningLawAgentOutput = await structured_llm.ainvoke(extraction_prompt)

    await log_agent_content(NodeName.ZONING_LAW_AGENT, "✅ Structured state updated. Passing control forward.")

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
async def _get_zoning_law_mock_response(state: OverallGraphState) -> dict:
    """
    Returns a static state-update payload mimicking a successful compliance registry tool database check,
    instantiated securely through ZoningLawAgentOutput for type-safety.
    """
    await log_agent_content(NodeName.ZONING_LAW_AGENT, "🔄 [MOCK] Zoning Law Agent: Using mock data")

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
                content="[MOCK] Zoning Law Agent: Using Mock data",
                name=NodeName.ZONING_LAW_AGENT.value
            )
        ]
    }