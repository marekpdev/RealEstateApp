import uuid

# Fixed, deterministic (not random uuid4) so every environment - a fresh dev
# database, CI, and a future migration that seeds this row - can refer to "the
# demo user" by the same literal id without passing it around at runtime.
DEMO_USER_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "demo-user.realestateapp")

# The native Postgres enum type name backing JobStatus. Declared once here so
# db/models.py and the migration that creates/drops the type reference the
# exact same string instead of two copies drifting apart.
JOB_STATUS_ENUM_NAME = "job_status"
