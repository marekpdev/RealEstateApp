from langchain_core.messages import AIMessage, HumanMessage
from langchain.agents import create_agent
from langchain_core.runnables import RunnableConfig
from config import NodeName
from config.config import MOCK_ZONING_LAW_AGENT_OUTPUT
from config.llm import base_model
from schema.state import OverallGraphState, ZoningLawAgentOutput
from tools import UnifiedMCPGateway, search_zoning_laws
from logger.lmm_translator import LogType, compile_ui_log
from logger.logger import log_agent_header, log_agent_content, log_agent_footer
from utils.utils import print_model, load_mock_fixture
from logger.callbacks import ToolLoggingCallbackHandler


_COMPILED_ZONING_AGENT = None


async def _get_compiled_zoning_agent():
    """
    Lazy-loads and caches the modern unified agent instance to prevent
    re-fetching MCP tools from the server on every graph node activation.
    """
    global _COMPILED_ZONING_AGENT
    if _COMPILED_ZONING_AGENT is not None:
        return _COMPILED_ZONING_AGENT

    tools_list = await UnifiedMCPGateway.get_tools(
        server_ids=["brave_search", "fetch_service"],
    )

    # 🌲 Pinecone Integration: Add the local vector search tool to the agent's arsenal.
    # This allows the agent to query verified municipal records before falling back to the web.
    tools_list.append(search_zoning_laws)

    system_instructions = (
        "You are an expert real estate compliance agent specializing in municipal zoning and land-use regulations. "
        "Your task is to research multi-family zoning rules for the city provided in the user message. "
        "This is a PET PROJECT; prioritize SPEED and SIMPLICITY over exhaustive research.\n\n"
        "⚠️ MANDATORY TOOL RULE:\n"
        "1. PREFER VERIFIED DATA: You MUST execute 'search_zoning_laws' (Pinecone) first. This is our primary source of truth.\n"
        "2. WEB FALLBACK: Only use 'brave_web_search' if 'search_zoning_laws' returns no results or insufficient data.\n"
        "3. SATISFACTION CRITERIA: As soon as you find ANY relevant zoning facts (even if incomplete), STOP and return them. "
        "Partial data is 100% acceptable. 'Good enough' is the goal.\n"
        "4. SEARCH BUDGET: Strictly limit yourself to 1 'search_zoning_laws' call and 1 'brave_web_search' call. "
        "Do NOT refine queries or search again if the first search yielded any useful snippets.\n"
        "5. FETCH BUDGET: Avoid 'fetch' if possible. Only fetch if both Pinecone and search snippets are empty. Max 1 fetch call.\n"
        "6. TOKEN ECONOMY: Stay within the 8000 token limit. Use 'count': 3 for searches.\n"
        "7. STOP CONDITION: Tool usage MUST cease as soon as you have a baseline understanding of the city's zoning.\n\n"
        "CRITICAL WORKFLOW:\n"
        "1. Call 'search_zoning_laws' for the target city.\n"
        "2. If Pinecone results are insufficient, call 'brave_web_search' once.\n"
        "3. Review all findings and populate the structured output immediately.\n"
        "4. Finish immediately. Do not loop back for more precision."
    )

    for tool in tools_list:
        tool.handle_tool_error = True

    _COMPILED_ZONING_AGENT = create_agent(
        model=base_model,
        tools=tools_list,
        system_prompt=system_instructions,
        response_format=ZoningLawAgentOutput,
    )
    return _COMPILED_ZONING_AGENT


async def zoning_law_agent_node(state: OverallGraphState) -> dict:
    """
    Zoning Law Agent: Prepares contextual search objectives and routes them
    through a unified agent harness to execute tool runs and extract a structured payload.
    """
    await log_agent_header(NodeName.ZONING_LAW_AGENT, "⚙️ Node: Zoning Law Agent")

    if MOCK_ZONING_LAW_AGENT_OUTPUT:
        return await _get_zoning_law_mock_response()

    agent = await _get_compiled_zoning_agent()

    target_city = state.ingest_input.city if state.ingest_input else "Unknown Market"

    agent_input = {
        "messages": [
            HumanMessage(content=f"Research multi-family zoning in: {target_city}")
        ]
    }

    await log_agent_content(
        NodeName.ZONING_LAW_AGENT,
        await compile_ui_log(
            LogType.NODE_START,
            NodeName.ZONING_LAW_AGENT.value,
            agent_input["messages"][0].content
        )
    )

    agent_config: RunnableConfig = {
        "callbacks": [ToolLoggingCallbackHandler(NodeName.ZONING_LAW_AGENT)],
        "recursion_limit": 10
    }

    agent_output = await agent.ainvoke(
        agent_input,
        config=agent_config
    )

    await log_agent_content(NodeName.ZONING_LAW_AGENT, "✅ Structured compilation complete. Updating graph state.")

    extraction_result: ZoningLawAgentOutput = agent_output["structured_response"]

    print_model(extraction_result)

    await log_agent_content(
        NodeName.ZONING_LAW_AGENT,
        await compile_ui_log(
            LogType.NODE_SUMMARY,
            NodeName.ZONING_LAW_AGENT.value,
            extraction_result.model_dump()
        )
    )

    await log_agent_footer(NodeName.ZONING_LAW_AGENT)

    return {
        "zoning_laws": extraction_result,
        "messages": [
            AIMessage(
                content="Zoning Law Agent: Completed live municipal registry scanning and structured regulatory parsing.",
                name=NodeName.ZONING_LAW_AGENT.value
            )
        ]
    }


async def _get_zoning_law_mock_response() -> dict:
    """
    Returns a static state-update payload mimicking a successful compliance registry tool database check,
    instantiated securely through ZoningLawAgentOutput for type-safety.
    """
    await log_agent_content(NodeName.ZONING_LAW_AGENT, "🔄 [MOCK] Zoning Law Agent: Using mock data")

    mock_payload = load_mock_fixture("mock_zoning_law_output_payload.json", ZoningLawAgentOutput)

    await log_agent_content(
        NodeName.ZONING_LAW_AGENT,
        await compile_ui_log(
            LogType.NODE_START,
            NodeName.ZONING_LAW_AGENT.value,
            "Using pre-configured system simulation parameters."
        )
    )

    await log_agent_content(
        NodeName.ZONING_LAW_AGENT,
        await compile_ui_log(
            LogType.NODE_SUMMARY,
            NodeName.ZONING_LAW_AGENT.value,
            mock_payload.model_dump()
        )
    )

    await log_agent_footer(NodeName.ZONING_LAW_AGENT)

    return {
        "zoning_laws": mock_payload,
        "messages": [
            AIMessage(
                content="[MOCK] Zoning Law Agent: Using Mock data",
                name=NodeName.ZONING_LAW_AGENT.value
            )
        ]
    }