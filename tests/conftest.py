import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.session as db_session_module
from config import config
from schema.state import OverallGraphState

REPO_ROOT = Path(__file__).resolve().parent.parent

@pytest.fixture
def mock_state():
    return OverallGraphState()

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
