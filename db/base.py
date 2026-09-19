from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Deterministic constraint/index names. Without this, Alembic autogenerate
# names constraints with an opaque hash (e.g. "sa_autoincrement_0"), and a
# later migration that wants to *alter* a specific constraint has nothing
# stable to reference it by.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
