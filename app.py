import asyncio

import chainlit as cl
from config import config
from db.enums import JobStatus
from logger.logger import log_message, render_financial_report
from services.report_api_client import ReportAPIError, get_client

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
    """The Chainlit-side dispatch, factored out of on_message() so it can be
    exercised directly in tests without needing a live Chainlit message/
    session context.

    There is no DB_PERSISTENCE_ENABLED branch here and no synchronous
    fallback that runs the graph inline: app.py no longer imports graph.py
    or the orchestration/persistence layer at all, so there is nothing left
    to fall back to. Submitting a report is always a real HTTP call to this
    same process's own /api/v1 surface (see services/report_api_client.py)
    - when persistence is off, the API itself answers 503 and that becomes
    a ReportAPIError here, surfaced to the user exactly like any other API
    failure. One path end to end, instead of one for Chainlit and a
    separate one for every other caller.
    """
    try:
        detail = await _submit_and_await(raw_query, idempotency_key)
    except ReportAPIError as exc:
        await log_message(f"⚠️ The API rejected this request: {exc.detail}")
        return
    except Exception as exc:
        await log_message(f"⚠️ Couldn't reach the API for this request: {exc}")
        return

    status = detail["status"]
    if status == JobStatus.FAILED.value:
        await log_message("⚠️ The analysis failed partway through. Please try again.")
        return
    if status != JobStatus.COMPLETED.value:
        await log_message(
            "⏳ This is taking longer than expected. The analysis is still running - check back shortly."
        )
        return

    # The graph runs in the Celery worker process, which has no live
    # Chainlit session - financial_modeler_agent_node's render-as-side-effect
    # can never reach this session. This is the only place that renders the
    # report to the user, fresh run or replay alike.
    report = detail.get("report")
    if report is not None:
        await render_financial_report(report["content"])


async def _submit_and_await(raw_query: str, idempotency_key: str) -> dict:
    """POSTs the request to /api/v1/reports (a fast call: one idempotent
    insert-or-select behind the scenes, same as before - see that route's
    own docstring), then polls GET /api/v1/reports/{id} until it reaches a
    terminal status or REPORT_POLL_TIMEOUT_SECONDS elapses. Postgres is no
    longer something this process talks to directly for any of this - the
    API is the only channel now, exactly as an external client would use
    it."""
    client = get_client()
    accepted = await client.create_report(raw_query, idempotency_key)
    return await _poll_until_terminal(client, accepted["id"])


async def _poll_until_terminal(client, request_id: str) -> dict:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + config.REPORT_POLL_TIMEOUT_SECONDS
    while True:
        detail = await client.get_report(request_id)
        if detail["status"] in (JobStatus.COMPLETED.value, JobStatus.FAILED.value):
            return detail
        if loop.time() >= deadline:
            return detail
        await asyncio.sleep(config.REPORT_POLL_INTERVAL_SECONDS)
