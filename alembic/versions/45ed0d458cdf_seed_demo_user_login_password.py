"""seed demo user login password

Revision ID: 45ed0d458cdf
Revises: 381da4338f07
Create Date: 2026-09-22 10:59:58.342830

"""
import uuid
from typing import Sequence, Union

import bcrypt
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '45ed0d458cdf'
down_revision: Union[str, Sequence[str], None] = '381da4338f07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Duplicated rather than imported from db.constants/auth.passwords, same as
# 681e83d80026_seed_demo_user.py's own DEMO_USER_ID/DEMO_USER_EMAIL: a
# migration is a frozen historical artifact and must not change meaning if
# application code changes underneath it later.
DEMO_USER_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "demo-user.realestateapp")
# 681e83d80026 could only seed an unusable placeholder hash, because no
# login flow existed yet to authenticate against it. Now that
# POST /api/v1/auth/login exists, the placeholder is replaced with a hash
# of this real, plaintext credential - the demo login for this app.
DEMO_USER_PASSWORD = "demo-password-123"

users_table = sa.table(
    "users",
    sa.column("id", sa.UUID()),
    sa.column("hashed_password", sa.String()),
)


def upgrade() -> None:
    """Upgrade schema."""
    hashed_password = bcrypt.hashpw(
        DEMO_USER_PASSWORD.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")
    op.execute(
        users_table.update()
        .where(users_table.c.id == DEMO_USER_ID)
        .values(hashed_password=hashed_password)
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Restores 681e83d80026's original deliberately-unusable placeholder.
    op.execute(
        users_table.update()
        .where(users_table.c.id == DEMO_USER_ID)
        .values(hashed_password="!unusable-seeded-by-migration")
    )
