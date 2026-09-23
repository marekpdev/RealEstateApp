import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# config.py (and, through it, server.py's CORSMiddleware construction and
# auth/api_keys.py's SERVICE_API_KEYS) reads CORS_ALLOWED_ORIGINS and
# SERVICE_API_KEYS once, at import time - whichever test module `import
# server` first, anywhere in this session, freezes both for the rest of
# the run. setdefault() so an explicit value in the real environment (CI,
# a developer's own .env) still wins; this only supplies a deterministic
# fallback so tests/test_cors.py and tests/test_api_keys.py don't depend
# on whatever happens to be ambient. Must run before `config`/`server` are
# imported anywhere below or in any test module.
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "https://allowed.example.com")
os.environ.setdefault("SERVICE_API_KEYS", "test-service-key")

import pytest
import pytest_asyncio
import redis
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.session as db_session_module
from config import config
from events.redis_client import dispose_events_redis_client
from schema.state import OverallGraphState

REPO_ROOT = Path(__file__).resolve().parent.parent

@pytest.fixture
def mock_state():
    return OverallGraphState()


@pytest_asyncio.fixture(autouse=True)
async def _dispose_events_redis_client_after_test():
    """events/redis_client.py's async singleton, like db/session.py's own
    engine, is only safe to dispose from the same event loop that built it.
    Every async test in this suite shares one session-scoped loop (this
    file's asyncio_default_fixture_loop_scope setting), but a plain
    synchronous test elsewhere (worker/tasks.py's generate_report, called
    directly - see tests/test_worker.py) opens its own fresh loop via
    asyncio.run() and disposes whatever it built inside that same call.
    Without this fixture, an async test that causes orchestration/
    run_recorder.py to publish a progress event (building the client on the
    shared session loop - e.g. tests/test_run_recorder.py's own progress-
    events test) would leave it cached there; the next sync test's dispose
    call would then try to close a connection bound to a different,
    still-open loop and crash with the exact "Future attached to a
    different loop" failure db/session.py's own engine already has to guard
    against - confirmed by dropping this fixture and running the suite,
    which reproduced precisely that crash in tests/test_worker.py.
    Disposing here, after every async test, keeps the singleton always torn
    down in the same loop that created it before any other test - sync or
    async - can inherit it."""
    yield
    await dispose_events_redis_client()


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets():
    """Every test in this suite shares one real Redis instance (this
    project always tests against real Postgres and real Redis, never mocks
    - tests/test_worker.py already connects to it directly for exactly this
    reason). Without this, report-endpoint calls made by other test files
    (test_api_reports.py, test_api_auth.py, test_cors.py, test_api_keys.py)
    would slowly draw down the same DEMO_USER_ID bucket the rate limiter
    enforces, eventually tripping a 429 in some unrelated, later test
    purely because of test order and cumulative request count across files
    - not anything that test itself did wrong. Flushing before every test
    (not just tests/test_rate_limiting.py) keeps every other file's
    existing tests exactly as request-count-agnostic as they were before
    the rate limiter existed - the same isolation goal db_session's
    per-test rollback already serves for Postgres, applied here to Redis
    instead.
    """
    client = redis.Redis.from_url(config.RATE_LIMIT_REDIS_URL)
    try:
        for key in client.scan_iter(match="ratelimit:*"):
            client.delete(key)
        yield
    finally:
        client.close()

@pytest.fixture
def mock_llm():
    """Fixture to mock the base LLM model."""
    with patch("config.llm.base_model", autospec=True) as mock:
        # Mock with_structured_output to return itself or a mock as needed by tests
        mock.with_structured_output.return_value = mock
        yield mock


def _test_database_url():
    """The dev DSN's database, renamed with a _test suffix - a dedicated
    database so these tests never touch dev data, on the same server/
    credentials the dev DSN already points at."""
    dev_url = make_url(config.POSTGRES_DSN)
    return dev_url.set(database=f"{dev_url.database}_test")


def _admin_database_url():
    """The 'postgres' maintenance database: CREATE DATABASE/DROP DATABASE
    cannot run against the database being created or dropped."""
    return make_url(config.POSTGRES_DSN).set(database="postgres")


async def _recreate_test_database(test_db_name: str) -> None:
    admin_engine = create_async_engine(_admin_database_url(), isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{test_db_name}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{test_db_name}"'))
    finally:
        await admin_engine.dispose()


async def _drop_test_database(test_db_name: str) -> None:
    admin_engine = create_async_engine(_admin_database_url(), isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{test_db_name}" WITH (FORCE)'))
    finally:
        await admin_engine.dispose()


def _run_alembic_upgrade(test_dsn: str) -> None:
    """Runs migrations against the test database as a real subprocess, not an
    in-process alembic.command call: alembic/env.py imports POSTGRES_DSN from
    config.config at module import time, so once that module has been
    imported once (by any other test) its value is frozen and monkeypatching
    os.environ afterwards would have no effect. A subprocess with an
    overridden environment always gets a fresh import.

    python -m alembic, not the alembic console script, since some local
    Windows setups block the console script via an Application Control
    policy while `python -m alembic` still works."""
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env={**os.environ, "POSTGRES_DSN": test_dsn},
        check=True,
    )


@pytest_asyncio.fixture(scope="session")
async def db_engine():
    """Session-scoped: a dedicated test database, migrated with Alembic
    (never create_all() - a drift test that compares metadata against a
    schema create_all() itself produced could never detect drift). Dropped
    and recreated at the start of the session so a previous run's leftover
    state (or a differently-shaped local dev database) can't leak in."""
    test_url = _test_database_url()
    await _recreate_test_database(test_url.database)

    test_dsn = test_url.render_as_string(hide_password=False)
    _run_alembic_upgrade(test_dsn)

    engine = create_async_engine(test_dsn)
    try:
        yield engine
    finally:
        await engine.dispose()
        await _drop_test_database(test_url.database)


@pytest_asyncio.fixture
async def db_session(db_engine):
    """Function-scoped: one connection, one outer transaction, rolled back at
    teardown - never truncation. Also rebinds db.session's module-level
    engine/sessionmaker singletons so application code that calls
    session_scope() internally (rather than taking a session as a parameter)
    transparently joins this same connection/transaction instead of opening
    its own connection to the real engine.

    join_transaction_mode="create_savepoint" is what makes this survive a
    commit(): committing this session (or one opened via session_scope()
    while it's rebound) only releases a SAVEPOINT nested inside the fixture's
    outer transaction, which this fixture rolls back at teardown - so a test
    that calls commit() still leaves no durable rows behind."""
    async with db_engine.connect() as connection:
        await connection.begin()
        test_sessionmaker = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        session = test_sessionmaker()

        original_engine = db_session_module._engine
        original_sessionmaker = db_session_module._sessionmaker
        db_session_module._engine = db_engine
        db_session_module._sessionmaker = test_sessionmaker

        try:
            yield session
        finally:
            await session.close()
            db_session_module._engine = original_engine
            db_session_module._sessionmaker = original_sessionmaker
            await connection.rollback()
