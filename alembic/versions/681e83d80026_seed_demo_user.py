"""seed demo user

Revision ID: 681e83d80026
Revises: a3d592816a15
Create Date: 2026-09-19 14:31:05.913324

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '681e83d80026'
down_revision: Union[str, Sequence[str], None] = 'a3d592816a15'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Duplicated here rather than imported from db.constants: a migration is a
# frozen historical artifact describing the exact row it inserted, and must
# not be able to change meaning if the application's constants module ever
# does. uuid5 is itself deterministic (RFC 4122) - recomputing it from the
# same fixed namespace and name string reproduces db.constants.DEMO_USER_ID's
# value exactly (4ff9b082-3ad6-5d44-891e-746afb229505), with nothing imported
# from application code.
DEMO_USER_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "demo-user.realestateapp")
DEMO_USER_EMAIL = "demo@realestateapp.local"
# No login flow exists yet, so there is no real password to hash. This is a
# deliberately unusable placeholder value, not a real hash, so a future auth
# implementation can never accidentally authenticate against it.
DEMO_USER_HASHED_PASSWORD = "!unusable-seeded-by-migration"

users_table = sa.table(
    "users",
    sa.column("id", sa.UUID()),
    sa.column("email", sa.String()),
    sa.column("hashed_password", sa.String()),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.bulk_insert(
        users_table,
        [
            {
                "id": DEMO_USER_ID,
                "email": DEMO_USER_EMAIL,
                "hashed_password": DEMO_USER_HASHED_PASSWORD,
            }
        ],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(users_table.delete().where(users_table.c.id == DEMO_USER_ID))
