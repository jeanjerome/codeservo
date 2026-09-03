from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from ..domain.constitution import (
    Constitution,
    ExecutionEnvironment,
    Gate,
    ResultFormat,
)
from ..domain.document import UNSET, Document, Unset
from ..domain.results import CommandResult, succeeded
from ..evidence.digests import sha256_file, sha256_record
from ..runtime.process import run_command
from ..runtime.sandbox import Isolation
from ..workspace.provider import Provider
from . import junit, lcov, observations, reports, sarif
from .observations import ObservationPathError

# What a gate writes inside the location the controller owns, and what the
# record keeps beside that gate's logs.
OBSERVATION_FILENAME = "observation.json"
OBSERVATION_SUFFIX = ".observation.json"

# The reader of every format the controller projects a document from, one
# entry each. The runner knows nothing else about any of them: it lists the
# files, hands the reader the ones this measurement wrote, and keeps what
# comes back the way it keeps a document a gate wrote itself.
PROJECTIONS = {
    ResultFormat.JUNIT_XML: junit.projection,
    ResultFormat.SARIF: sarif.projection,
    ResultFormat.LCOV: lcov.projection,
}


@dataclass(frozen=True)
class KeptObservation:
    """Where the record keeps a gate's document, and what it digests to."""

    path: str | None = None
    sha256: str | None = None


@dataclass(frozen=True, kw_only=True)
class UnsignedGateResult(Document):
    """One gate's measurement, before it closes over itself.

    The four observation fields exist only for a gate that declared it answers
    with a document: a gate reporting its exit code alone has nothing to say
    about one, and stays silent rather than saying null. `passed` follows from
    the exit code and the timeout, so a result cannot report a verdict the
    measurement it carries does not reach.
    """

    name: str
    command: str
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    stdout_path: str
    stdout_sha256: str
    stderr_path: str
    stderr_sha256: str
    result_format: ResultFormat
    observation_status: observations.Classification | Unset = UNSET
    observation_error: str | None | Unset = UNSET
    observation_path: str | None | Unset = UNSET
    observation_sha256: str | None | Unset = UNSET
    passed: bool = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "passed", succeeded(self.exit_code, self.timed_out))

    def signed(self) -> GateResult:
        """This measurement, closed over the digest of what it holds.

        The digest is taken over the document, so it covers what the record
        will carry: the verdict and the observation of a gate that made one,
        and neither the digest itself nor a location the run chose.
        """
        carried = {
            declared.name: getattr(self, declared.name)
            for declared in fields(UnsignedGateResult)
            if declared.init
        }
        return GateResult(**carried, result_sha256=sha256_record(self.to_document()))


@dataclass(frozen=True, kw_only=True)
class GateResult(UnsignedGateResult):
    """One gate's measurement, and the digest recomputable from what it holds."""

    result_sha256: str


def gate_command(
    gate: Gate,
    *,
    tree: Path,
    execution: ExecutionEnvironment | None,
    observation: Path | None = None,
    provider: Provider | None = None,
) -> str:
    """The command one gate runs against the tree it measures.

    A task gate names the manifest of that tree and nothing else: the source
    repository during the baseline, the isolated checkout afterwards. The
    constitution supplies the task name, never the command line around it.

    A gate answering with a document is told where to write it, and the two
    kinds of gate are told differently because only one channel reaches each.
    A command gate reads the location from its environment. A task gate cannot:
    the provider runs it with a clean environment, so the location is appended
    to the command as the task's one argument.
    """
    if gate.task is None:
        if gate.command is None:
            raise ValueError(f"gate {gate.name}: declares neither command nor task")
        return gate.command
    if execution is None or provider is None:
        raise ValueError(f"gate {gate.name}: task requires an execution provider")
    return provider.task_command(
        manifest=tree / execution.manifest,
        environment=execution.environment,
        task=gate.task,
        arguments=() if observation is None else (str(observation),),
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
    return raw, KeptObservation(path=str(kept), sha256=sha256_file(kept))


def run_gates(
    *,
    repo: Path,
    gates: tuple[Gate, ...],
    out_dir: Path,
    sensor_paths: dict[str, Path] | None = None,
    isolation: Isolation = Isolation(),
    execution: ExecutionEnvironment | None = None,
    run_dir: Path | None = None,
    provider: Provider | None = None,
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
        gate_env = (
            provider.measurement_environment(repo / execution.manifest)
            if execution is not None and provider is not None
            else {}
        )
        if sensor_path is not None:
            gate_env["CODESERVO_SENSOR_PATH"] = str(sensor_path)
        # Only a gate that declared the format is told where to write, and the
        # location exists before that gate runs.
        location: Path | None = None
        document: Path | None = None
        if gate.result_format == ResultFormat.CODESERVO_JSON:
            location = _observation_location(gate, forbidden)
            document = location / OBSERVATION_FILENAME
            gate_env[observations.OBSERVATION_PATH_VARIABLE] = str(document)
        # A gate whose tool writes its reports in the tree is told nothing:
        # what the tree holds before it runs is listed, so that only what it
        # wrote is read afterwards.
        before: reports.Listing | None = None
        if gate.result_format.reads_reports and gate.reports is not None:
            before = reports.list_reports(repo, gate.reports)
        result = run_command(
            name=gate.name,
            command=gate_command(
                gate,
                tree=repo,
                execution=execution,
                observation=document,
                provider=provider,
            ),
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
        results.append(
            _measured(
                result, gate, location, out_dir, tree=repo, before=before
            ).signed()
        )
    return results


def _measured(
    result: CommandResult,
    gate: Gate,
    location: Path | None,
    out_dir: Path,
    *,
    tree: Path | None = None,
    before: reports.Listing | None = None,
) -> UnsignedGateResult:
    """One gate's measurement, with the document it wrote if it declared one."""
    measured = UnsignedGateResult(
        name=result.name,
        command=result.command,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        duration_ms=result.duration_ms,
        stdout_path=result.stdout_path,
        stdout_sha256=result.stdout_sha256,
        stderr_path=result.stderr_path,
        stderr_sha256=result.stderr_sha256,
        result_format=gate.result_format,
    )
    if before is not None and tree is not None and gate.reports is not None:
        return _projected(measured, result, gate, tree, before, out_dir)
    if location is None:
        return measured

    written = location / OBSERVATION_FILENAME
    raw: bytes | None = None
    kept = KeptObservation()
    if written.is_file():
        raw, kept = _kept_observation(written, out_dir, gate.name)
    status, error = observations.classify(raw, passed=result.passed)
    # The record now holds the only copy.
    _remove(location)
    # Flat beside the logs, never nested: `sha256_record` drops only top-level
    # keys ending in `_path`, so a location one level deeper would leave
    # `result_sha256` unable to recompute from the record.
    return replace(
        measured,
        observation_status=status,
        observation_error=error,
        observation_path=kept.path,
        observation_sha256=kept.sha256,
    )


def _projected(
    measured: UnsignedGateResult,
    result: CommandResult,
    gate: Gate,
    tree: Path,
    before: reports.Listing,
    out_dir: Path,
) -> UnsignedGateResult:
    """The reports the gate wrote, projected onto the document the record keeps.

    The projection is held to the same contract as a document a gate writes
    itself, and kept the same way, beside that gate's logs. A report the
    reader of that format cannot make sense of is a fault of the measurement.
    A gate that passed and wrote no report measured nothing anyone can see,
    and says so; one that failed and wrote none failed before its tool
    reported anything, and the document says that instead.
    """
    pattern = gate.reports or ""
    written, left = reports.written_reports(tree, pattern, before)
    if not written and result.passed:
        untouched = (
            f"; {len(left)} matched and predate this measurement" if left else ""
        )
        return replace(
            measured,
            observation_status=observations.Classification.ABSENT,
            observation_error=(
                f"the gate passed and wrote no report matching {pattern}{untouched}"
            ),
            observation_path=None,
            observation_sha256=None,
        )
    try:
        document = PROJECTIONS[gate.result_format](
            tree,
            written,
            sensor=measured.name,
            passed=result.passed,
            pattern=pattern,
            left=len(left),
        )
    except reports.ReportFault as fault:
        return replace(
            measured,
            observation_status=observations.Classification.INVALID,
            observation_error=str(fault),
            observation_path=None,
            observation_sha256=None,
        )
    raw = reports.render(document)
    kept = out_dir / f"{measured.name}{OBSERVATION_SUFFIX}"
    kept.write_bytes(raw)
    status, error = observations.classify(raw, passed=result.passed)
    return replace(
        measured,
        observation_status=status,
        observation_error=error,
        observation_path=str(kept),
        observation_sha256=sha256_file(kept),
    )


def baseline_gates(constitution: Constitution) -> tuple[Gate, ...]:
    return tuple(g for g in constitution.gates if g.baseline)
