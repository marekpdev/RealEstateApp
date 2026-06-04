from langchain_core.messages import HumanMessage
import chainlit as cl
from config import config
from graph import compiledStateGraph
from logger.logger import log_message

@cl.on_chat_start
async def on_chat_start():
    """Initializes the session when a recruiter opens the webpage."""
    await cl.Message(
        content="👋 **Real Estate AI Investment Planner**. Set your investment parameters (like intended city and max budget) to watch the multi-agent graph route your strategy live!"
    ).send()
    if config.DEBUG_MODE:
        debug_message = f"""
        ** DEBUG MODE ENABLED **
        - ENV VARS -
        MOCK_FINANCIAL_MODELER_AGENT_OUTPUT - {config.MOCK_FINANCIAL_MODELER_AGENT_OUTPUT}
        MOCK_INGEST_INPUT_AGENT_OUTPUT - {config.MOCK_INGEST_INPUT_AGENT_OUTPUT}
        MOCK_MARKET_DATA_AGENT_OUTPUT - {config.MOCK_MARKET_DATA_AGENT_OUTPUT}
        MOCK_NEIGHBORHOOD_VIBE_AGENT_OUTPUT - {config.MOCK_NEIGHBORHOOD_VIBE_AGENT_OUTPUT}
        MOCK_ZONING_LAW_AGENT_OUTPUT - {config.MOCK_ZONING_LAW_AGENT_OUTPUT}
        MOCK_MARKET_DATA_API - {config.MOCK_MARKET_DATA_API}
        """
        await log_message(debug_message)


@cl.on_message
async def on_message(message: cl.Message):
    """
    Triggered every time a user types into the chatbox.
    This replaces your __main__ manual text query loops.
    """
    inputs = {"messages": [HumanMessage(content=message.content)]}
    config = {"recursion_limit": 20}

    await compiledStateGraph.ainvoke(inputs, config=config)