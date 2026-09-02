from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import NotRequired, TypedDict

from ..domain.constitution import (
    CODESERVO_JSON,
    Constitution,
    ExecutionEnvironment,
    Gate,
    ResultFormat,
)
from ..evidence.digests import sha256_file, sha256_record
from ..runtime.process import run_command
from ..runtime.sandbox import Isolation
from ..workspace import pixi
from . import observations
from .observations import ObservationPathError

# What a gate writes inside the location the controller owns, and what the
# record keeps beside that gate's logs.
OBSERVATION_FILENAME = "observation.json"
OBSERVATION_SUFFIX = ".observation.json"


class KeptObservation(TypedDict):
    """Where the record keeps a gate's document, and what it digests to."""

    observation_path: str | None
    observation_sha256: str | None


class UnsignedGateResult(TypedDict):
    """One gate's measurement, before it closes over itself.

    The four observation fields exist only for a gate that declared it answers
    with a document: a gate reporting its exit code alone has nothing to say
    about one, and says nothing rather than saying null.
    """

    name: str
    command: str
    passed: bool
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    stdout_path: str
    stdout_sha256: str
    stderr_path: str
    stderr_sha256: str
    result_format: ResultFormat
    observation_status: NotRequired[observations.Classification]
    observation_error: NotRequired[str | None]
    observation_path: NotRequired[str | None]
    observation_sha256: NotRequired[str | None]


class GateResult(UnsignedGateResult):
    """One gate's measurement, and the digest recomputable from what it holds."""

    result_sha256: str


def gate_command(
    gate: Gate, *, tree: Path, execution: ExecutionEnvironment | None
) -> str:
    """The command one gate runs against the tree it measures.

    A task gate names the manifest of that tree and nothing else: the source
    repository during the baseline, the isolated checkout afterwards. The
    constitution supplies the task name, never the command line around it.
    """
    if gate.task is None:
        if gate.command is None:
            raise ValueError(f"gate {gate.name}: declares neither command nor task")
        return gate.command
    if execution is None:
        raise ValueError(f"gate {gate.name}: task requires an execution provider")
    return pixi.task_command(
        manifest=tree / execution.manifest,
        environment=execution.environment,
        task=gate.task,
    )


def _observation_location(gate: Gate, forbidden: tuple[Path, ...]) -> Path:
    """A directory of the controller's, for one gate to write one document in.

    Where a temporary directory lands is chosen by the environment the
    controller runs in, not by this call, so where it landed is checked. The
    record no gate may write and the tree no gate may modify are both refused,
    and a refused directory is removed before anything ran in it: neither the
    record nor the measured tree keeps a trace of the location.
    """
    directory = Path(tempfile.mkdtemp(prefix="codeservo-observation-")).resolve()
    for owned in forbidden:
        anchor = owned.resolve()
        if directory == anchor or directory.is_relative_to(anchor):
            _remove(directory)
            raise ObservationPathError(
                f"gate {gate.name}: observation location {directory} must lie"
                f" outside {anchor}"
            )
    return directory


def _remove(directory: Path) -> None:
    """Remove a controller-owned location, or say that it is still there."""
    try:
        shutil.rmtree(directory)
    except OSError as exc:
        raise ObservationPathError(
            f"observation location {directory} could not be removed: {exc}"
        ) from exc


def _kept_observation(
    written: Path, out_dir: Path, name: str
) -> tuple[bytes, KeptObservation]:
    """Copy what the gate wrote, byte for byte, beside that gate's logs.

    Nothing is reparsed, reindented or reordered on the way in: the digest is
    the digest of the bytes the gate produced, and the record holds those bytes.
    """
    raw = written.read_bytes()
    kept = out_dir / f"{name}{OBSERVATION_SUFFIX}"
    kept.write_bytes(raw)
    return raw, {
        "observation_path": str(kept),
        "observation_sha256": sha256_file(kept),
    }


def run_gates(
    *,
    repo: Path,
    gates: tuple[Gate, ...],
    out_dir: Path,
    sensor_paths: dict[str, Path] | None = None,
    isolation: Isolation = Isolation(),
    execution: ExecutionEnvironment | None = None,
    run_dir: Path | None = None,
) -> list[GateResult]:
    results: list[GateResult] = []
    sensors = sensor_paths or {}
    # The two locations no gate may write into: the record of the run, and the
    # tree the gates are measuring.
    forbidden = (run_dir if run_dir is not None else out_dir, repo)
    for gate in gates:
        sensor_path = sensors.get(gate.name)
        if gate.sensor is not None and sensor_path is None:
            raise ValueError(f"missing frozen sensor for gate {gate.name}")
        # Every gate of a run that declares a provider is a measurement, task
        # gate or not: none of them may resolve or install, so none of them can
        # change the environment they are all measured in. A run declaring no
        # provider sets nothing.
        gate_env = pixi.measurement_environment() if execution is not None else {}
        if sensor_path is not None:
            gate_env["CODESERVO_SENSOR_PATH"] = str(sensor_path)
        # Only a gate that declared the format is told where to write, and the
        # location exists before that gate runs.
        location: Path | None = None
        if gate.result_format == CODESERVO_JSON:
            location = _observation_location(gate, forbidden)
            gate_env[observations.OBSERVATION_PATH_VARIABLE] = str(
                location / OBSERVATION_FILENAME
            )
        result = run_command(
            name=gate.name,
            command=gate_command(gate, tree=repo, execution=execution),
            cwd=repo,
            out_dir=out_dir,
            timeout_seconds=gate.timeout_seconds,
            env=gate_env or None,
            unset_env=(
                "CODESERVO_SENSOR_PATH",
                observations.OBSERVATION_PATH_VARIABLE,
            ),
            isolation=isolation,
        )
        record: UnsignedGateResult = {
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
            "result_format": gate.result_format,
        }
        if location is not None:
            written = location / OBSERVATION_FILENAME
            raw: bytes | None = None
            kept: KeptObservation = {
                "observation_path": None,
                "observation_sha256": None,
            }
            if written.is_file():
                raw, kept = _kept_observation(written, out_dir, gate.name)
            status, error = observations.classify(raw, passed=result.passed)
            # Flat beside the logs, never nested: `sha256_record` drops only
            # top-level keys ending in `_path`, so a location one level deeper
            # would leave `result_sha256` unable to recompute from the record.
            record["observation_status"] = status
            record["observation_error"] = error
            record["observation_path"] = kept["observation_path"]
            record["observation_sha256"] = kept["observation_sha256"]
            # The record now holds the only copy.
            _remove(location)
        results.append({**record, "result_sha256": sha256_record(record)})
    return results


def baseline_gates(constitution: Constitution) -> tuple[Gate, ...]:
    return tuple(g for g in constitution.gates if g.baseline)
