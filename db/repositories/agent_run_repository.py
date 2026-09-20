import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.enums import JobStatus
from db.models import AgentRun
from db.repositories.base import BaseRepository


class AgentRunRepository(BaseRepository):
    async def upsert(
        self,
        *,
        request_id: uuid.UUID,
        node_name: str,
        status: JobStatus,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        error_message: Optional[str] = None,
        output: Optional[dict] = None,
    ) -> AgentRun:
        """Insert one row per (request_id, node_name), or update the
        existing row's already-supplied fields on conflict - a node that
        runs more than once for the same request (e.g. a replayed run)
        updates its one row instead of accumulating a second.

        Only fields the caller actually passed a value for are written on
        the UPDATE branch: a call that only updates `status` must not wipe
        `output`/`error_message` a fuller, earlier call for the same node
        already set.
        """
        values = {"request_id": request_id, "node_name": node_name, "status": status}
        optional = {
            "started_at": started_at,
            "completed_at": completed_at,
            "error_message": error_message,
            "output": output,
        }
        supplied = {key: value for key, value in optional.items() if value is not None}
        values.update(supplied)

        stmt = pg_insert(AgentRun).values(**values)
        update_columns = {"status": stmt.excluded.status}
        update_columns.update({key: getattr(stmt.excluded, key) for key in supplied})
        stmt = stmt.on_conflict_do_update(
            index_elements=[AgentRun.request_id, AgentRun.node_name],
            set_=update_columns,
        ).returning(AgentRun.id)

        # Deliberately .returning(AgentRun.id) + session.get(), not
        # .returning(AgentRun): SQLAlchemy's ORM-enabled INSERT...RETURNING
        # row-to-object population assumes a genuinely new row per input
        # value set, and when the row's primary key is already present in
        # this session's identity map (exactly the ON CONFLICT DO UPDATE
        # case - the row already existed), it hands back the stale cached
        # object instead of one reflecting the just-updated columns.
        # populate_existing=True forces a real refresh from the row this
        # statement just wrote.
        row_id = await self.session.scalar(stmt)
        return await self.session.get(AgentRun, row_id, populate_existing=True)

    async def list_by_request_id(self, request_id: uuid.UUID) -> List[AgentRun]:
        result = await self.session.execute(
            select(AgentRun).where(AgentRun.request_id == request_id)
        )
        return list(result.scalars().all())
