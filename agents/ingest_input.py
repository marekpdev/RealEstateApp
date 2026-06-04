from langchain_core.messages import SystemMessage, AIMessage, HumanMessage

from config import NodeName
from config.config import MOCK_INGEST_INPUT_AGENT_OUTPUT
from config.llm import base_model
from logger.lmm_translator import LogType, compile_ui_log
from schema.state import OverallGraphState, IngestInputAgentOutput
from logger.logger import log_agent_header, log_agent_content, log_agent_footer


async def ingest_input_agent_node(state: OverallGraphState) -> dict:
    """
    Ingest Input Agent: Extracts and validates user input for market analysis.
    This agent processes the user's natural language input to extract the targeted location and maximum budget allocation.
    It ensures that the extracted data is valid and structured according to the defined schema.
    """
    await log_agent_header(NodeName.INGEST_INPUT_AGENT, "⚙️ Node: Ingest Input Agent")

    if MOCK_INGEST_INPUT_AGENT_OUTPUT:
        return await _get_ingest_mock_response()

    # Step 1: Retrieve the raw human prompt from the input
    user_message_content = state.messages[-1].content

    await log_agent_content(
        NodeName.INGEST_INPUT_AGENT,
        await compile_ui_log(
            LogType.NODE_START,
            NodeName.INGEST_INPUT_AGENT.value,
            user_message_content
        )
    )

    # Step 2: Bind the extraction schema to your LLM configuration
    structured_llm = base_model.with_structured_output(IngestInputAgentOutput)

    # Step 3: Run the model to perform entity extraction
    extraction_result: IngestInputAgentOutput = structured_llm.invoke([
        SystemMessage(
            content=(
                "You are a strict data-extraction gateway for a real estate investment engine. "
                "Your sole job is to parse the user's natural language input and extract "
                "the targeted location and maximum budget allocation matching the requested schema. "
                "Do not assume or extrapolate values if they are completely missing."
            )
        ),
        HumanMessage(
            content=f"Analyze and extract fields from this prompt: {user_message_content}"
        )
    ])

    await log_agent_content(
        NodeName.INGEST_INPUT_AGENT,
        await compile_ui_log(
            LogType.NODE_SUMMARY,
            NodeName.INGEST_INPUT_AGENT.value,
            extraction_result.model_dump()
        )
    )

    # Step 4: Construct and return the state update payload nested under the correct state key
    await log_agent_footer(NodeName.INGEST_INPUT_AGENT)

    return {
        "ingest_input": extraction_result,
        "messages": [
            AIMessage(
                content=f"Ingest Node: Parsed city as '{extraction_result.city}' and budget as '{extraction_result.budget}'. Tracking parameters initialized.",
                name=NodeName.INGEST_INPUT_AGENT.value
            )
        ]
    }


# --- PRIVATELY SCORED MOCK PROVIDER ---
async def _get_ingest_mock_response() -> dict:
    """
    Returns a static state-update payload verified by IngestInputAgentOutput,
    nested under the correct key for OverallGraphState compatibility.
    """
    await log_agent_content(NodeName.INGEST_INPUT_AGENT, "🔄 [MOCK] Ingest Input Agent: Using mock data")

    # Instantiate the structured output object explicitly
    mock_payload = IngestInputAgentOutput(
        city="Los Angeles, CA",
        budget="$800,000"
    )

    await log_agent_content(
        NodeName.INGEST_INPUT_AGENT,
        await compile_ui_log(
            LogType.NODE_START,
            NodeName.INGEST_INPUT_AGENT.value,
            "Using pre-configured system simulation parameters."
        )
    )

    await log_agent_content(
        NodeName.INGEST_INPUT_AGENT,
        await compile_ui_log(
            LogType.NODE_SUMMARY,
            NodeName.INGEST_INPUT_AGENT.value,
            mock_payload.model_dump()
        )
    )

    # Return the validated Pydantic object directly under 'ingest_input'
    await log_agent_footer(NodeName.INGEST_INPUT_AGENT)

    return {
        "ingest_input": mock_payload,
        "messages": [
            AIMessage(
                content=f"[MOCK] Ingest Input Agent: Parsed targeted market as '{mock_payload.city}' and set investment ceiling to '{mock_payload.budget}'.",
                name=NodeName.INGEST_INPUT_AGENT.value
            )
        ]
    }