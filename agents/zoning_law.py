from langchain.agents.structured_output import ToolStrategy
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from langchain.agents import create_agent
from langchain_core.runnables import RunnableConfig

from config import NodeName, config
from config.config import MOCK_ZONING_LAW_AGENT_OUTPUT
from config.llm import base_model
from schema.state import OverallGraphState, ZoningLawAgentOutput
from tools.tools import UnifiedMCPGateway
from utils.logger import log_agent_header, log_agent_content
from utils.utils import print_model

_COMPILED_ZONING_AGENT = None

# TODO generally it works but is doing multiple loops
# e.g.
# Tool 'brave_web_search' is being used. Args: {'query': 'Los Angeles zoning multi-family property upgrades', 'count': 10, 'offset': 0}
# 5+ Tool 'fetch' is being used. Args: (taken from previous brave_web_search)
# Tool 'brave_web_search' is being used. Args: {'query': 'Los Angeles multifamily property zoning regulations upgrades', 'count': 10, 'offset': 0}
# 5+ Tool 'fetch' is being used. Args: (taken from previous brave_web_search)
# Tool 'brave_web_search' is being used. Args: {'query': 'Los Angeles multi-family zoning upgrade ordinances 2026', 'count': 10, 'offset': 0}
# 5+ Tool 'fetch' is being used. Args: (taken from previous brave_web_search)
# so I am burning through tokens a lot, so I need to use mini models for cheap results, or better, some free GitHub model
async def _get_compiled_zoning_agent():
    """
    Lazy-loads and caches the modern unified agent instance to prevent
    re-fetching MCP tools from the server on every graph node activation.
    """
    global _COMPILED_ZONING_AGENT
    if _COMPILED_ZONING_AGENT is not None:
        return _COMPILED_ZONING_AGENT

    # Dynamically fetch compliant tool structures from the live MCP gateway.
    # We now filter by server ID to allow the agent access to all tools
    # hosted on the 'brave_search' and 'fetch_service' servers.
    tools_list = await UnifiedMCPGateway.get_tools(
        server_ids=["brave_search", "fetch_service"],
    )

    system_instructions = (
        "You are an expert real estate compliance agent.\n\n"
        "⚠️ MANDATORY TOOL RULE:\n"
        "You have ZERO internal knowledge about the target city. You MUST execute 'brave_web_search' "
        "at least once to find live 2026 data. Do not generate the final response from memory.\n\n"
        "CRITICAL WORKFLOW:\n"
        "1. Call 'brave_web_search' to get live municipal registry text.\n"
        "2. Use 'fetch' if any references or URLs cut off.\n"
        "3. Populate the final structured output schema strictly using facts gathered from these tool calls."
    )

    # Instantiate the modern unified agent framework
    for tool in tools_list:
        tool.handle_tool_error = True

    _COMPILED_ZONING_AGENT = create_agent(
        model=base_model,
        tools=tools_list,
        system_prompt=system_instructions,
        # 🎯 The agent natively handles the validation loop on exit and saves
        # the verified Pydantic model directly into the 'structured_response' key.
        response_format=ZoningLawAgentOutput,
    )
    return _COMPILED_ZONING_AGENT


# =====================================================================
# 2. RUNTIME GRAPH NODE LAYER
# =====================================================================

async def zoning_law_agent_node(state: OverallGraphState) -> dict:
    """
    Zoning Law Agent: Prepares contextual search objectives and routes them
    through a unified agent harness to execute tool runs and extract a structured payload.
    """
    await log_agent_header(NodeName.ZONING_LAW_AGENT, "⚙️ Node: Zoning Law Agent")

    # Handle Mock Execution
    if MOCK_ZONING_LAW_AGENT_OUTPUT:
        return await _get_zoning_law_mock_response(state)

    # 1. Fetch the globally cached agent harness
    agent = await _get_compiled_zoning_agent()

    target_city = state.ingest_input.city if state.ingest_input else "Unknown Market"

    # 2. Construct a clean starting input state for this agent run.
    # By passing the custom prompt right here, we make the global compilation
    # fully dynamic for any city provided at runtime.
    agent_input = {
        "messages": [
            HumanMessage(content=f"Perform a comprehensive zoning search for multi-family property upgrades in {target_city}.")
        ]
    }

    await log_agent_content(NodeName.ZONING_LAW_AGENT, "🤖 Booting agent harness & scanning municipal registries...")

    # 3. Invoke the worker agent with our starting context package
    agent_config: RunnableConfig = {
        "callbacks": [
            type("ToolLogger", (AsyncCallbackHandler,), {"on_tool_start": log_tool})()
        ]
    }

    agent_output = await agent.ainvoke(
        agent_input,
        config=agent_config
    )

    await log_agent_content(NodeName.ZONING_LAW_AGENT, "✅ Structured compilation complete. Updating graph state.")

    # 4. Extract the validated Pydantic object from the standardized output channel
    extraction_result: ZoningLawAgentOutput = agent_output["structured_response"]

    print_model(extraction_result)

    return {
        "zoning_laws": extraction_result,
        # Append node status updates to your parent graph state trace
        "messages": [
            AIMessage(
                content="Zoning Law Agent: Completed live municipal registry scanning and structured regulatory parsing.",
                name=NodeName.ZONING_LAW_AGENT.value
            )
        ]
    }

async def log_tool(self, serialized, input_str, **kwargs):
        print(f"DEBUG LOGGING TOOL {serialized.get('name', 'Unknown')}")
        await log_agent_content(
            NodeName.ZONING_LAW_AGENT,
            f"🛠️ Tool '{serialized.get('name', 'Unknown')}' is being used. Args: {input_str}"
        )

# =====================================================================
# 3. PRIVATELY SCORED MOCK PROVIDER
# =====================================================================

async def _get_zoning_law_mock_response(state: OverallGraphState) -> dict:
    """
    Returns a static state-update payload mimicking a successful compliance registry tool database check,
    instantiated securely through ZoningLawAgentOutput for type-safety.
    """
    await log_agent_content(NodeName.ZONING_LAW_AGENT, "🔄 [MOCK] Zoning Law Agent: Using mock data")

    target_city = state.ingest_input.city if state.ingest_input else "Unknown Market"

    mock_zoning_result = (
        f"Municipal Ordinance & Land-Use Registry Check for {target_city}:\n"
        "- Brickell (Zone T6-48-O / High-Density Core): Allows maximum 48 stories. Mixed-use commercial/residential overlay. "
        "Short-term rentals (STR) restricted to buildings with specific hotel licensing.\n"
        "- Wynwood (Zone NRD-1 / Neighborhood Revitalization District): 5-story height cap enforced to preserve neighborhood scale. "
        "Live-work units encouraged. STR permitted with strict transient occupancy taxes.\n"
        "Policy Notice: City council passing new transit-oriented development guidelines next quarter."
    )

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