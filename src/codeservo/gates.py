from __future__ import annotations

from pathlib import Path

from .evidence import sha256_record
from .model import Constitution, Gate
from .process import run_command
from .sandbox import Isolation


def run_gates(
    *,
    repo: Path,
    gates: tuple[Gate, ...],
    out_dir: Path,
    sensor_paths: dict[str, Path] | None = None,
    isolation: Isolation = Isolation(),
) -> list[dict]:
    results: list[dict] = []
    sensors = sensor_paths or {}
    for gate in gates:
        sensor_path = sensors.get(gate.name)
        if gate.sensor is not None and sensor_path is None:
            raise ValueError(f"missing frozen sensor for gate {gate.name}")
        result = run_command(
            name=gate.name,
            command=gate.command,
            cwd=repo,
            out_dir=out_dir,
            timeout_seconds=gate.timeout_seconds,
            env=(
                {"CODESERVO_SENSOR_PATH": str(sensor_path)}
                if sensor_path is not None
                else None
            ),
            unset_env=("CODESERVO_SENSOR_PATH",),
            isolation=isolation,
        )
        record = {
            "name": result.name,
            "command": result.command,
            "passed": result.passed,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "duration_ms": result.duration_ms,
            "stdout_path": result.stdout_path,
            "stdout_sha256": result.stdout_sha256,
            "stderr_path": result.stderr_path,
            "stderr_sha256": result.stderr_sha256,
        }
        record["result_sha256"] = sha256_record(record)
        results.append(record)
    return results


def baseline_gates(constitution: Constitution) -> tuple[Gate, ...]:
    return tuple(g for g in constitution.gates if g.baseline)
