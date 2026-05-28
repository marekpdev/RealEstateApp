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
from utils.utils import print_model, load_mock_fixture

_COMPILED_ZONING_AGENT = None

# Generally it works. We have optimized loops and fetch budget to prevent token overflow.
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
        "You are an expert real estate compliance agent specializing in municipal zoning and land-use regulations. "
        "Your task is to research multi-family zoning rules for the city provided in the user message. "
        "This is a PET PROJECT; prioritize SPEED and SIMPLICITY over exhaustive research.\n\n"
        "⚠️ MANDATORY TOOL RULE:\n"
        "1. ZERO INTERNAL KNOWLEDGE: You MUST execute 'brave_web_search' at least once. Do NOT use memory.\n"
        "2. SATISFACTION CRITERIA: As soon as you find ANY relevant zoning facts (even if incomplete), STOP and return them. "
        "Partial data is 100% acceptable. 'Good enough' is the goal.\n"
        "3. SEARCH BUDGET: Strictly limit yourself to 1-2 'brave_web_search' calls. Do NOT refine queries or search again "
        "if the first search yielded any useful snippets. Do NOT obsess over finding 2026 data specifically.\n"
        "4. FETCH BUDGET: Avoid 'fetch' if possible. Only fetch if search snippets are completely empty. Max 1 fetch call.\n"
        "5. TOKEN ECONOMY: Stay within the 8000 token limit. Use 'count': 3 for searches.\n"
        "6. STOP CONDITION: Tool usage MUST cease as soon as you have a baseline understanding of the city's zoning.\n\n"
        "CRITICAL WORKFLOW:\n"
        "1. Call 'brave_web_search' once. Review results.\n"
        "2. If snippets provide ANY details on multi-family rules, immediately populate the structured output.\n"
        "3. Only use 'fetch' as a last resort if snippets are non-existent.\n"
        "4. Finish immediately. Do not loop back for more precision."
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
        return await _get_zoning_law_mock_response()

    # 1. Fetch the globally cached agent harness
    agent = await _get_compiled_zoning_agent()

    target_city = state.ingest_input.city if state.ingest_input else "Unknown Market"

    # 2. Construct a clean starting input state for this agent run.
    # We pass the target city as the primary task; the cached agent 
    # handles the behavioral constraints via its internal system instructions.
    agent_input = {
        "messages": [
            HumanMessage(content=f"Research multi-family zoning in: {target_city}")
        ]
    }

    await log_agent_content(NodeName.ZONING_LAW_AGENT, "🤖 Booting agent harness & scanning municipal registries...")

    # 3. Invoke the worker agent with our starting context package
    agent_config: RunnableConfig = {
        "callbacks": [
            type(
                "ToolLogger",
                (AsyncCallbackHandler,),
                {
                    "on_tool_start": log_tool_start,
                    "on_tool_end": log_tool_end
                }
            )()
        ],
        "recursion_limit": 5
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

async def log_tool_start(self, serialized, input_str, **kwargs):
        print(f"DEBUG log_tool_start {serialized.get('name', 'Unknown')}")
        await log_agent_content(
            NodeName.ZONING_LAW_AGENT,
            f"🛠️ Tool '{serialized.get('name', 'Unknown')}' is being used. Args: {input_str}"
        )


async def log_tool_end(self, output, **kwargs):
    """
    Triggers automatically when an MCP tool finishes execution.
    Splits long outputs to prevent messy logs.
    """
    print(f"DEBUG log_tool_end")
    output_str = str(output)

    # Optional: Truncate what you print to the console so 50k tokens don't spam your terminal
    preview_limit = 1000
    if len(output_str) > preview_limit:
        preview = output_str[:preview_limit] + f"\n... [Truncated for preview, total size: {len(output_str)} chars] ..."
    else:
        preview = output_str

    await log_agent_content(
        NodeName.ZONING_LAW_AGENT,
        f"📥 Tool Execution Complete. Output Response:\n{preview}"
    )

# =====================================================================
# 3. PRIVATELY SCORED MOCK PROVIDER
# =====================================================================

async def _get_zoning_law_mock_response() -> dict:
    """
    Returns a static state-update payload mimicking a successful compliance registry tool database check,
    instantiated securely through ZoningLawAgentOutput for type-safety.
    """
    await log_agent_content(NodeName.ZONING_LAW_AGENT, "🔄 [MOCK] Zoning Law Agent: Using mock data")

    mock_payload = load_mock_fixture("mock_zoning_law_output_payload.json", ZoningLawAgentOutput)

    return {
        "zoning_laws": mock_payload,
        "messages": [
            AIMessage(
                content="[MOCK] Zoning Law Agent: Using Mock data",
                name=NodeName.ZONING_LAW_AGENT.value
            )
        ]
    }