"""What a run is about, independent of how any of it is measured.

Nothing here reads a file, starts a process or knows a backend. These are the
values every other layer is written in terms of, and the direction every
dependency in the package points in.
"""

from .constitution import (
    Constitution,
    ExecutionEnvironment,
    Gate,
    Phase,
    ResultFormat,
    ReviewPolicy,
    ScopePolicy,
)
from .results import CommandResult, SensorResult
from .run import RunStatus
from .task import Task, TaskError, load_task

__all__ = [
    "CommandResult",
    "Constitution",
    "ExecutionEnvironment",
    "Gate",
    "Phase",
    "ResultFormat",
    "ReviewPolicy",
    "RunStatus",
    "ScopePolicy",
    "SensorResult",
    "Task",
    "TaskError",
    "load_task",
]
