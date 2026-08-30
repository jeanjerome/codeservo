from __future__ import annotations

from pathlib import Path

from .model import Constitution, Gate
from .process import run_command


def run_gates(
    *,
    repo: Path,
    gates: tuple[Gate, ...],
    out_dir: Path,
) -> list[dict]:
    results: list[dict] = []
    for gate in gates:
        result = run_command(
            name=gate.name,
            command=gate.command,
            cwd=repo,
            out_dir=out_dir,
            timeout_seconds=gate.timeout_seconds,
        )
        results.append(
            {
                "name": result.name,
                "command": result.command,
                "passed": result.passed,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "duration_ms": result.duration_ms,
                "stdout_path": result.stdout_path,
                "stderr_path": result.stderr_path,
            }
        )
    return results


def baseline_gates(constitution: Constitution) -> tuple[Gate, ...]:
    return tuple(g for g in constitution.gates if g.baseline)
