import uuid

import pytest
from sqlalchemy import select

from db.constants import DEMO_USER_ID
from db.enums import JobStatus
from db.models import InvestmentRequest


def _make_request(idempotency_key: str) -> InvestmentRequest:
    return InvestmentRequest(
        user_id=DEMO_USER_ID,
        idempotency_key=idempotency_key,
        status=JobStatus.PENDING,
        city="Testville",
        budget="$500k",
    )


@pytest.mark.asyncio
async def test_committed_row_is_invisible_on_a_separate_connection(db_engine, db_session):
    """The rigorous proof of isolation: db_session's commit() only releases a
    SAVEPOINT nested inside a transaction the fixture itself opened and never
    commits. A genuinely separate connection - one that never joined that
    transaction - must not see the row at all, committed or not, because the
    outer transaction holding it is still open."""
    key = f"isolation-check-{uuid.uuid4()}"
    db_session.add(_make_request(key))
    await db_session.commit()

    async with db_engine.connect() as other_connection:
        result = await other_connection.execute(
            select(InvestmentRequest.id).where(InvestmentRequest.idempotency_key == key)
        )
        assert result.first() is None


@pytest.mark.asyncio
async def test_previous_test_left_no_rows_behind(db_session):
    """The practical guarantee day-to-day tests rely on: once a test using
    db_session ends, its fixture teardown rolls back the outer transaction,
    so the next test starts against a table with none of the previous test's
    committed rows - the seed migration's demo user is the only durable row
    that predates every test in this session."""
    result = await db_session.execute(select(InvestmentRequest))
    assert result.scalars().all() == []
