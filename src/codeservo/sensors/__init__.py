"""What measures a candidate, and what a measurement is allowed to say.

A sensor reports; it never decides. Gates run confined to the tree they
measure, scope reads the diff against the frozen base commit, and an
observation is the document a gate may write beside its exit code.
"""

from .gates import baseline_gates, gate_command, run_gates
from .scope import changed_files, diff_line_count, scope_sensor

__all__ = [
    "baseline_gates",
    "changed_files",
    "diff_line_count",
    "gate_command",
    "run_gates",
    "scope_sensor",
]
