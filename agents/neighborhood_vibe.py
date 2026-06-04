from langchain_core.messages import AIMessage, HumanMessage
from langchain.agents import create_agent
from langchain_core.runnables import RunnableConfig

from config import NodeName
from config.config import MOCK_NEIGHBORHOOD_VIBE_AGENT_OUTPUT
from config.llm import base_model
from schema.state import OverallGraphState, NeighborhoodVibeAgentOutput
from tools import UnifiedMCPGateway
from logger.lmm_translator import LogType, compile_ui_log
from logger.logger import log_agent_header, log_agent_content, log_agent_footer
from utils.utils import print_model, load_mock_fixture
from logger.callbacks import ToolLoggingCallbackHandler

_COMPILED_NEIGHBORHOOD_VIBE_AGENT = None


async def _get_compiled_neighborhood_vibe_agent():
    """
    Lazy-loads and caches the modern unified agent instance to prevent
    re-fetching MCP tools from the server on every graph node activation.
    """
    global _COMPILED_NEIGHBORHOOD_VIBE_AGENT
    if _COMPILED_NEIGHBORHOOD_VIBE_AGENT is not None:
        return _COMPILED_NEIGHBORHOOD_VIBE_AGENT

    # Dynamically fetch compliant tool structures from the live MCP gateway.
    # Access tools hosted on openstreetmap, wikipedia, and free_web_search servers.
    tools_list = await UnifiedMCPGateway.get_tools(
        server_ids = ["wikipedia_service", "openstreetmap_service"],
        disallowed_tool_names=["suggest_meeting_point"] # filter out problematic tools (schema errors etc)
    )

    system_instructions = (
        "You are a local community researcher and socio-demographic analyst. "
        "Your job is to synthesize local community sentiment, lifestyle trends, "
        "transit metrics, and regional profiling for a given city location.\n\n"
        "🛠️ AVAILABLE TOOLS:\n"
        "1. OpenStreetMap (openstreetmap_service): Use this to find physical amenities, parks, transit nodes, and local geography infrastructure.\n"
        "2. Wikipedia (wikipedia_service): Use this to research the history, macro-demographics, and general profiling of the area.\n\n"
        "⚠️ MANDATORY TOOL RULES:\n"
        "1. ANTI-RATE LIMIT DISCIPLINE (HTTP 429 PREVENTION): You MUST execute OpenStreetMap tools sequentially, never concurrently in a single turn. Pause between geospatial queries. Do not request more than 2 sub-regions or amenity lookups at a time.\n"
        "2. DATA SYNTHESIS: Gather enough information to provide a descriptive, objective assessment.\n"
        "3. NO RAG: You do not have access to internal vector databases; rely solely on the provided MCP tools.\n"
        "4. SPEED & SIMPLICITY: Prioritize 'good enough' data over exhaustive research. 2-3 tool calls total are usually sufficient.\n"
        "5. TOKEN ECONOMY: Be mindful of token limits. Limit the result set or depth parameters on geographic tools to keep payloads compact.\n"
        "6. STOP CONDITION: Tool usage MUST cease as soon as you have a baseline understanding of the city's vibe.\n\n"
        "CRITICAL WORKFLOW:\n"
        "1. First, call Wikipedia to establish a macro historical, cultural, and demographic baseline of the target neighborhoods.\n"
        "2. Next, use OpenStreetMap sequentially to sample localized transit availability and point-of-interest (POI) density.\n"
        "3. Review all findings and populate the structured output immediately. Do not loop endlessly."
    )

    for tool in tools_list:
        tool.handle_tool_error = True

    _COMPILED_NEIGHBORHOOD_VIBE_AGENT = create_agent(
        model=base_model,
        tools=tools_list,
        system_prompt=system_instructions,
        # The agent handles the validation loop and saves the verified model to 'structured_response'
        response_format=NeighborhoodVibeAgentOutput,
    )
    return _COMPILED_NEIGHBORHOOD_VIBE_AGENT


async def neighborhood_vibe_agent_node(state: OverallGraphState) -> dict:
    """
    Neighborhood Vibe Agent: Coordinates semantic context lookup regarding social demographics.
    Uses MCP tools (Wikipedia, OpenStreetMap, Web Search) to synthesize local sentiment and lifestyle.
    """
    await log_agent_header(NodeName.NEIGHBORHOOD_VIBE_AGENT, "⚙️ Node: Neighborhood Vibe Agent")

    if MOCK_NEIGHBORHOOD_VIBE_AGENT_OUTPUT:
        return await _get_neighborhood_vibe_mock_response()

    # 1. Fetch the globally cached agent harness
    agent = await _get_compiled_neighborhood_vibe_agent()

    target_city = state.ingest_input.city if state.ingest_input else "Unknown Market"

    # 2. Construct a clean starting input state for this agent run.
    agent_input = {
        "messages": [
            HumanMessage(content=f"Analyze local sentiment, lifestyle vibes, and risk factors for neighborhoods inside '{target_city}'.")
        ]
    }

    await log_agent_content(
        NodeName.NEIGHBORHOOD_VIBE_AGENT,
        await compile_ui_log(
            LogType.NODE_START,
            NodeName.NEIGHBORHOOD_VIBE_AGENT.value,
            agent_input["messages"][0].content
        )
    )

    # 3. Invoke the worker agent with our starting context package
    agent_config: RunnableConfig = {
        "callbacks": [ToolLoggingCallbackHandler(NodeName.NEIGHBORHOOD_VIBE_AGENT)],
        "recursion_limit": 10
    }

    agent_output = await agent.ainvoke(
        agent_input,
        config=agent_config
    )

    await log_agent_content(NodeName.NEIGHBORHOOD_VIBE_AGENT, "✅ Structured compilation complete. Updating graph state.")

    # 4. Extract the validated Pydantic object from the standardized output channel
    extraction_result: NeighborhoodVibeAgentOutput = agent_output["structured_response"]

    print_model(extraction_result)

    await log_agent_content(
        NodeName.NEIGHBORHOOD_VIBE_AGENT,
        await compile_ui_log(
            LogType.NODE_SUMMARY,
            NodeName.NEIGHBORHOOD_VIBE_AGENT.value,
            extraction_result.model_dump()
        )
    )

    await log_agent_footer(NodeName.NEIGHBORHOOD_VIBE_AGENT)

    return {
        "neighborhood_vibe": extraction_result,
        "messages": [
            AIMessage(
                content="Neighborhood Vibe Agent: Completed live tool search and structured sentiment extraction.",
                name=NodeName.NEIGHBORHOOD_VIBE_AGENT.value
            )
        ]
    }

# --- PRIVATELY SCORED MOCK PROVIDER ---
async def _get_neighborhood_vibe_mock_response() -> dict:
    """
    Returns a static state-update payload mimicking a successful MCP tool research run,
    instantiated securely through NeighborhoodVibeAgentOutput for type-safety.
    """
    await log_agent_content(NodeName.NEIGHBORHOOD_VIBE_AGENT, "🔄 [MOCK] Neighborhood Vibe Agent: Using mock data")

    mock_payload = load_mock_fixture("mock_neighborhood_vibe_output_payload.json", NeighborhoodVibeAgentOutput)

    await log_agent_content(
        NodeName.NEIGHBORHOOD_VIBE_AGENT,
        await compile_ui_log(
            LogType.NODE_START,
            NodeName.NEIGHBORHOOD_VIBE_AGENT.value,
            "Using pre-configured system simulation parameters."
        )
    )

    await log_agent_content(
        NodeName.NEIGHBORHOOD_VIBE_AGENT,
        await compile_ui_log(
            LogType.NODE_SUMMARY,
            NodeName.NEIGHBORHOOD_VIBE_AGENT.value,
            mock_payload.model_dump()
        )
    )

    await log_agent_footer(NodeName.NEIGHBORHOOD_VIBE_AGENT)

    return {
        "neighborhood_vibe": mock_payload,
        "messages": [
            AIMessage(
                content="[MOCK] Neighborhood Vibe Agent: Using Mock data",
                name=NodeName.NEIGHBORHOOD_VIBE_AGENT.value
            )
        ]
    }