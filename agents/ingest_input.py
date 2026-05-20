from langchain_core.messages import SystemMessage, AIMessage, HumanMessage

from config import NodeName
from config.llm import base_model
from pydantic import BaseModel, Field
from schema.state import OverallGraphState

class IngestInputAgentOutput(BaseModel):
    city: str = Field(..., description="The city extracted from the user's input")
    budget: str = Field(..., description="The maximum budget extracted from the user's input")

use_mock = True

def ingest_input_agent_node(state: OverallGraphState):
    if use_mock:
        return _get_ingest_mock_response()

    # Step 1: Retrieve the raw human prompt from the input
    # Because of the reducer, state.messages is guaranteed to contain the user's chat input
    user_message_content = state.messages[-1].content

    # Step 2: Bind the extraction schema to your LLM configuration
    # This enforces that the JSON output mathematically matches IngestAgentOutput
    structured_llm = base_model.with_structured_output(IngestInputAgentOutput)

    # Step 3: Run the model to perform entity extraction
    extraction_result = structured_llm.invoke([
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

    # Step 4: Construct and return the state update payload
    # Note: We also append an AI message to the logs to keep track of what the Ingest agent did
    return {
        "city": extraction_result.city,
        "budget": extraction_result.budget,
        "messages": [
            AIMessage(
                content=f"Ingest Node: Parsed city as '{extraction_result.city}' and budget as '{extraction_result.budget}'. Tracking parameters initialized.",
                name=NodeName.INGEST_INPUT_AGENT.value
            )
        ]
    }

def _get_ingest_mock_response() -> dict:
    """
    Returns a static state-update payload mimicking a
    successful structural extraction.
    """
    mock_city = "Miami, FL"
    mock_budget = "$600,000"

    return {
        "city": mock_city,
        "budget": mock_budget,
        "messages": [
            AIMessage(
                content=f"[MOCK] Ingest Node: Parsed targeted market as '{mock_city}' and set investment ceiling to '{mock_budget}'.",
                name=NodeName.INGEST_INPUT_AGENT.value
            )
        ]
    }