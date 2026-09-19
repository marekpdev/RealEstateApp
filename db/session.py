from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import config

# Module-level lazy singletons, not app.state: Chainlit's handlers run inside a
# *separate* FastAPI app mounted via mount_chainlit(), which never sees the
# top-level app's state. A module-level singleton is reachable from anywhere
# that imports this module, session or Chainlit handler alike.
_engine: Optional[AsyncEngine] = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


def get_engine() -> AsyncEngine:
    """Returns the process-wide AsyncEngine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            config.POSTGRES_DSN,
            pool_size=config.DB_POOL_SIZE,
            max_overflow=config.DB_MAX_OVERFLOW,
            echo=config.DB_ECHO,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Returns the process-wide session factory, creating it on first use."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """
    One unit of work: yields a session, commits it on clean exit, and rolls back
    and re-raises on any exception. Callers should not call commit()/rollback()
    themselves - the scope owns the transaction boundary.
    """
    session = get_sessionmaker()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def dispose_engine() -> None:
    """Disposes the engine's connection pool and clears the singletons. Call on
    application shutdown so no connection outlives the process that opened it."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
