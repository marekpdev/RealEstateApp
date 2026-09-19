from enum import Enum


class JobStatus(str, Enum):
    """
    The closed set of states a unit of tracked work (an investment request, or
    one agent's run within it) can be in. Backed by a native Postgres ENUM, not
    VARCHAR, because this domain is fixed by the application's own state
    machine, not by something external that changes shape (contrast
    config.constants.NodeName, which stays VARCHAR because graph topology
    changes are exactly the kind of change an ENUM makes expensive).
    """
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
