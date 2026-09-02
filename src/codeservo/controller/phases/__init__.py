"""The phases of a run, in the order the controller drives them.

Each phase is handed the frozen context and the open record. It measures, it
writes what it measured, and it either lets the run continue or raises the
rejection that ends it. None of them decides how a run is closed.
"""

from .baseline import create_candidate, measure_baseline
from .environment import freeze_execution_environment, prepare_candidate_environment
from .full import measure_full
from .iteration import Converged, converge
from .review import review_candidate

__all__ = [
    "Converged",
    "converge",
    "create_candidate",
    "freeze_execution_environment",
    "measure_baseline",
    "measure_full",
    "prepare_candidate_environment",
    "review_candidate",
]
