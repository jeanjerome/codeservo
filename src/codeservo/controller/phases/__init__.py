"""The phases of a run, in the order the controller drives them.

Each phase is handed the frozen context and the open record. It measures, it
writes what it measured, and it either lets the run continue or raises the
rejection that ends it. None of them decides how a run is closed.
"""

from .baseline import create_candidate, measure_baseline
from .environment import freeze_execution_environment, prepare_candidate_environment
from .iteration import converge

__all__ = [
    "converge",
    "create_candidate",
    "freeze_execution_environment",
    "measure_baseline",
    "prepare_candidate_environment",
]
