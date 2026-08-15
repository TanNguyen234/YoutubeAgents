"""YouTube Autopilot — Autonomous Content Orchestration System.

Core package initialization.
"""

from enum import Enum

__version__ = "0.1.0"
__all__ = ["__version__", "ExecutionState"]


class ExecutionState(str, Enum):
    """Explicit runtime execution states per project contract."""

    REAL = "REAL"
    TEST = "TEST"
    DRY_RUN = "DRY_RUN"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
