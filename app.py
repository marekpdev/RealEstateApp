from langchain_core.messages import HumanMessage
import chainlit as cl
from config import config
from db.constants import DEMO_USER_ID
from db.enums import JobStatus
from graph import compiledStateGraph
from logger.logger import log_message, render_financial_report
from orchestration.run_recorder import RunOutcome, claim_request, get_replayed_outcome, poll_until_terminal
from worker.tasks import generate_report

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
        outcome = await _enqueue_and_await(raw_query, idempotency_key)
    except Exception as exc:
        await log_message(f"⚠️ Couldn't record this request in the database: {exc}")
        return

    if outcome.status == JobStatus.FAILED:
        await log_message("⚠️ The analysis failed partway through. Please try again.")
        return
    if outcome.status != JobStatus.COMPLETED:
        await log_message(
            "⏳ This is taking longer than expected. The analysis is still running - check back shortly."
        )
        return

    # The graph now always runs in the Celery worker process (see
    # worker/tasks.py's generate_report), which has no live Chainlit
    # session - financial_modeler_agent_node's render-as-side-effect can
    # never reach this session anymore, fresh run or replay alike. This is
    # the only place that renders the report to the user now.
    if outcome.report is not None:
        await render_financial_report(outcome.report.content)


async def _enqueue_and_await(raw_query: str, idempotency_key: str) -> RunOutcome:
    """Claims the request synchronously (fast: one idempotent insert-or-
    select, see claim_request()'s docstring), then either replays an
    already-completed report or enqueues the slow work onto the Celery
    worker and polls the job row for a terminal status."""
    request_id, should_run = await claim_request(DEMO_USER_ID, idempotency_key)
    if not should_run:
        return await get_replayed_outcome(request_id)

    generate_report.delay(raw_query, str(request_id), 20)
    return await poll_until_terminal(request_id)