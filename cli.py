import asyncio
import uuid

from langchain_core.messages import HumanMessage

from config import config
from db.constants import DEMO_USER_ID
from db.enums import JobStatus
from graph import compiledStateGraph
from logger.logger import render_financial_report
from orchestration.run_recorder import RunOutcome, claim_request, get_replayed_outcome, poll_until_terminal
from worker.tasks import generate_report


async def run_cli_pipeline():
    print("🚀 Starting App in CLI Mode...")

    """Asynchronous orchestrator for terminal-based LangGraph testing."""
    query = input("Ask the Agent: ")

    # Query for testing only
    # query = "I would like to invest in Los Angeles, CA, and my max budget is $800,000"

    # No client-supplied message id here (unlike Chainlit) - each terminal
    # invocation is its own request, so a fresh key per run is correct rather
    # than accidentally replaying a previous CLI session's job.
    await handle_query(query, idempotency_key=str(uuid.uuid4()))

    print("\n🏁 App Finished Successfully.")


async def handle_query(raw_query: str, *, idempotency_key: str) -> None:
    """The persistence-aware dispatch, factored out of run_cli_pipeline() so
    it can be exercised directly against a real database in tests without
    needing to mock input()/print()."""
    if not config.DB_PERSISTENCE_ENABLED:
        inputs = {"messages": [HumanMessage(content=raw_query)]}
        await compiledStateGraph.ainvoke(inputs, config={"recursion_limit": 20})
        return

    try:
        outcome = await _enqueue_and_await(raw_query, idempotency_key)
    except Exception as exc:
        print(f"⚠️ Couldn't record this request in the database: {exc}")
        return

    if outcome.status == JobStatus.FAILED:
        print("⚠️ The analysis failed partway through. Please try again.")
        return
    if outcome.status != JobStatus.COMPLETED:
        print("⏳ This is taking longer than expected. The analysis is still running - check back shortly.")
        return

    # The graph now always runs in the Celery worker process (see
    # worker/tasks.py's generate_report), which has no live Chainlit
    # session - financial_modeler_agent_node's render-as-side-effect can
    # never reach this process anymore, fresh run or replay alike. This is
    # the only place that renders the report to the user now.
    if outcome.report is not None:
        await render_financial_report(outcome.report.content)


async def _enqueue_and_await(raw_query: str, idempotency_key: str) -> RunOutcome:
    """Claims the request synchronously (fast: one idempotent insert-or-
    select, see claim_request()'s docstring), then either replays an
    already-completed report or enqueues the slow work onto the Celery
    worker and polls the job row for a terminal status."""
    claim = await claim_request(DEMO_USER_ID, idempotency_key, raw_query)
    if not claim.should_run:
        return await get_replayed_outcome(claim.request_id)

    generate_report.delay(raw_query, str(claim.request_id), 20)
    return await poll_until_terminal(claim.request_id)


if __name__ == "__main__":
    asyncio.run(run_cli_pipeline())