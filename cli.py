import asyncio
import uuid

from langchain_core.messages import HumanMessage

from config import config
from db.constants import DEMO_USER_ID
from db.enums import JobStatus
from graph import compiledStateGraph
from logger.logger import render_financial_report
from orchestration.run_recorder import execute_and_record


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
        outcome = await execute_and_record(
            raw_query,
            user_id=DEMO_USER_ID,
            idempotency_key=idempotency_key,
            recursion_limit=20,
        )
    except Exception as exc:
        print(f"⚠️ Couldn't record this request in the database: {exc}")
        return

    if outcome.status == JobStatus.FAILED:
        print("⚠️ The analysis failed partway through. Please try again.")
        return

    # A fresh run already rendered its report as a side effect inside
    # financial_modeler_agent_node; only a replay (the graph never ran) needs
    # it rendered explicitly here, or the user would never see it twice.
    if outcome.replayed and outcome.report is not None:
        await render_financial_report(outcome.report.content)


if __name__ == "__main__":
    asyncio.run(run_cli_pipeline())