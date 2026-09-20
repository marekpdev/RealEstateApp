from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository:
    """Holds the AsyncSession a repository method runs its statements
    against. The repository never opens or closes that session itself
    (session_scope() in db/session.py owns that boundary) - it only issues
    statements on the one it's handed.

    A method that catches IntegrityError to arbitrate a race (see
    InvestmentRequestRepository.create_idempotent()) must be the *only*
    thing its session does in that unit of work: the rollback() such a
    method issues discards every pending change on the session, not just
    its own insert, because SQLAlchemy async sessions don't scope a flush
    failure to a begin_nested() savepoint (verified empirically - see the
    class docstring there). Callers must not mix such a call with other
    pending writes on the same session.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
