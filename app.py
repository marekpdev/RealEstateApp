from langchain_core.messages import HumanMessage
import chainlit as cl
from config import config
from db.constants import DEMO_USER_ID
from db.enums import JobStatus
from graph import compiledStateGraph
from logger.logger import log_message, render_financial_report
from orchestration.run_recorder import execute_and_record

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
        OFFLINE_MODE - {config.OFFLINE_MODE}
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
    await handle_query(message.content, idempotency_key=message.id)


async def handle_query(raw_query: str, *, idempotency_key: str) -> None:
    """The persistence-aware dispatch, factored out of on_message() so it can
    be exercised directly against a real database in tests without needing a
    live Chainlit message/session context."""
    if not config.DB_PERSISTENCE_ENABLED:
        inputs = {"messages": [HumanMessage(content=raw_query)]}
        await compiledStateGraph.ainvoke(inputs, config={"recursion_limit": 20})
        return

    try:
        outcome = await execute_and_record(
            raw_query,
            user_id=DEMO_USER_ID,
            idempotency_key=idempotency_key,
            recursion_limit=20,
        )
    except Exception as exc:
        await log_message(f"⚠️ Couldn't record this request in the database: {exc}")
        return

    if outcome.status == JobStatus.FAILED:
        await log_message("⚠️ The analysis failed partway through. Please try again.")
        return

    # A fresh run already rendered its report as a side effect inside
    # financial_modeler_agent_node; only a replay (the graph never ran) needs
    # it rendered explicitly here, or the user would never see it twice.
    if outcome.replayed and outcome.report is not None:
        await render_financial_report(outcome.report.content)