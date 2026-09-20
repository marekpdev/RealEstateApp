import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

from db.base import Base


def _diff_against_live_schema(sync_connection):
    context = MigrationContext.configure(sync_connection)
    return compare_metadata(context, Base.metadata)


@pytest.mark.asyncio
async def test_no_migration_drift(db_engine):
    """Guards against the failure mode migrations exist to prevent: a model
    changed in db/models.py without a matching migration, or a migration
    hand-edited (e.g. the enum lifecycle fix) so it no longer produces the
    schema the models describe. Compares the live, migrated test database
    against Base.metadata the same way `alembic revision --autogenerate`
    would - an empty diff is exactly what "a second --autogenerate produces
    an empty migration" (verified manually for the initial schema) means as
    an automated, permanent check."""
    async with db_engine.connect() as connection:
        diff = await connection.run_sync(_diff_against_live_schema)

    assert diff == [], f"Migration drift detected between models and migrations: {diff}"
