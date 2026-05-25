from langchain_core.messages import SystemMessage, AIMessage, HumanMessage

from config import NodeName
from config.config import MOCK_INGEST_INPUT_AGENT_OUTPUT
from config.llm import base_model
from pydantic import BaseModel, Field
from schema.state import OverallGraphState, IngestInputAgentOutput

def ingest_input_agent_node(state: OverallGraphState) -> dict:
    if MOCK_INGEST_INPUT_AGENT_OUTPUT:
        return _get_ingest_mock_response()

    # Step 1: Retrieve the raw human prompt from the input
    user_message_content = state.messages[-1].content

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

    # Step 4: Construct and return the state update payload nested under the correct state key
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
def _get_ingest_mock_response() -> dict:
    """
    Returns a static state-update payload verified by IngestInputAgentOutput,
    nested under the correct key for OverallGraphState compatibility.
    """
    # Instantiate the structured output object explicitly
    mock_payload = IngestInputAgentOutput(
        city="Los Angeles, CA",
        budget="$800,000"
    )

    # Return the validated Pydantic object directly under 'ingest_input'
    return {
        "ingest_input": mock_payload,
        "messages": [
            AIMessage(
                content=f"[MOCK] Ingest Node: Parsed targeted market as '{mock_payload.city}' and set investment ceiling to '{mock_payload.budget}'.",
                name=NodeName.INGEST_INPUT_AGENT.value
            )
        ]
    }