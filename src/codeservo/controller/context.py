"""Everything a run is fixed to before it measures anything.

The arguments arrive, the control inputs are read and frozen, the record is
opened, and from then on nothing in this object changes. Every phase is
written against it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..actuators import Actuator, default_actuator_name, load_actuator
from ..actuators.catalogue import Catalogue, CatalogueError, Effort, load_catalogue
from ..domain.constitution import Constitution, ExecutionEnvironment
from ..domain.run import RunStatus
from ..domain.task import Task, load_task
from ..evidence.digests import sha256_text
from ..evidence.journal import JOURNAL_NAME, Journal
from ..policies.constitution import load_constitution
from ..workspace.git import common_git_dir, head, root
from ..workspace.provider import Provider, load_provider
from .document import Decision, EnvironmentBlock, Evidence, FrozenSensor
from .errors import ControlFailure
from .freeze import freeze_sensors
from .inference import InferenceRequest, frozen_inference, roles
from .isolation import Confinement, confinement
from .provenance import runtime_metadata
from .record import EVIDENCE_SCHEMA_VERSION, RunRecord, utc_now

# What a run measures through when its constitution declares no execution
# provider: whatever the host offers, and the block says so.
NO_PROVIDER = "none"

DEFAULT_STATE_DIRECTORY = ".codeservo"


def declared_environment(constitution: Constitution) -> EnvironmentBlock:
    """The environment block a run opens its record with.

    The provider is the one the constitution declares, so a run refused
    before the execution table is read closes on a block agreeing with the
    constitution the record hashed rather than on a constant denying it.
    Nothing else is asserted about an environment nothing has measured yet:
    every other field stays unset until the reading that establishes it.
    """
    execution = constitution.execution
    return EnvironmentBlock(
        provider=NO_PROVIDER if execution is None else execution.provider
    )


def undeclared_gates(task: Task, constitution: Constitution) -> list[str]:
    """Criteria naming a gate this constitution does not declare.

    A criterion names the control that decides it, and a name no gate answers
    to would leave it decided by nobody: the reviewer is not asked about it,
    and no measurement carries it. The two control inputs are held to each
    other before either is frozen, rather than after a run has spent an
    actuation on a criterion nothing was going to read.
    """
    declared = {gate.name for gate in constitution.gates}
    return [
        f"criterion {criterion.id} names gate {criterion.gate},"
        " which the constitution does not declare"
        for criterion in task.criteria.values()
        if criterion.gate is not None and criterion.gate not in declared
    ]


@dataclass(frozen=True)
class RunRequest:
    """What one invocation asked for, before any of it is resolved."""

    repo_path: Path
    task_path: Path
    max_iterations: int
    agent_timeout_seconds: int
    state_dir: Path | None
    actuator: str | None
    model: str
    effort: str
    review_actuator: str | None
    review_model: str
    review_effort: str


@dataclass(frozen=True)
class RunContext:
    """The frozen control inputs of one run, and the locations it owns."""

    request: RunRequest
    repo: Path
    task: Task
    constitution: Constitution
    catalogue: Catalogue
    provider: Provider | None
    base_commit: str
    run_id: str
    state_root: Path
    run_dir: Path
    worktree: Path
    implementer: Actuator
    reviewer: Actuator
    confinement: Confinement
    sensor_paths: dict[str, Path]
    sensor_evidence: dict[str, FrozenSensor]

    @property
    def execution(self) -> ExecutionEnvironment | None:
        return self.constitution.execution


def _effort(value: str, role: str) -> Effort:
    """One of the four efforts, or a refusal naming what was asked."""
    try:
        return Effort(value)
    except ValueError:
        known = ", ".join(Effort)
        raise ControlFailure(
            f"{role} effort must be one of {known}, not {value!r}"
        ) from None


def _profile(
    catalogue: Catalogue, actuator: Actuator, model: str, effort: str, role: str
) -> InferenceRequest:
    """One role's request, held to the catalogue before anything is frozen.

    A model the catalogue does not list for that backend, or lists for the
    other one, is refused by name here: the run directory does not exist yet,
    so nothing records a run that never had a profile to run on.
    """
    try:
        catalogue.lookup(actuator.name, model)
    except CatalogueError as exc:
        raise ControlFailure(f"{role} profile: {exc}") from None
    return InferenceRequest(
        backend=actuator.name, model=model, effort=_effort(effort, role)
    )


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def resolve_state_dir(repo: Path, state_dir: Path | None) -> Path:
    root_dir = (
        state_dir.expanduser().resolve()
        if state_dir is not None
        else (Path.home() / DEFAULT_STATE_DIRECTORY).resolve()
    )
    if root_dir == repo or root_dir.is_relative_to(repo):
        raise ControlFailure("state directory must be outside the target repository")
    return root_dir


def prepare(request: RunRequest) -> tuple[RunContext, RunRecord]:
    """Read every control input, open the record, and freeze the run to both.

    Nothing is measured here and no candidate exists yet. What this leaves
    behind is a run directory holding the task, the constitution and the
    sensors as they were, and a journal already carrying them.
    """
    implementer = load_actuator(request.actuator or default_actuator_name())
    # The reviewer backend is loaded on its own, so a run can implement with
    # one command-line tool and decide with another. Asking for neither leaves
    # the implementer's backend serving both roles.
    reviewer = load_actuator(request.review_actuator or implementer.name)
    catalogue = load_catalogue()
    inference = frozen_inference(
        implementer=_profile(
            catalogue, implementer, request.model, request.effort, "implementer"
        ),
        reviewer=_profile(
            catalogue, reviewer, request.review_model, request.review_effort, "reviewer"
        ),
    )

    repo = root(request.repo_path)
    task = load_task(request.task_path.resolve())
    constitution = load_constitution(repo)
    unknown = undeclared_gates(task, constitution)
    if unknown:
        raise ControlFailure("; ".join(unknown))
    base_commit = head(repo)
    run_id = new_run_id()
    state_root = resolve_state_dir(repo, request.state_dir)
    # The provider a constitution declares is loaded by name, and handed the
    # directory the controller owns for it, before anything is frozen to it.
    provider = (
        load_provider(constitution.execution.provider, state_root)
        if constitution.execution is not None
        else None
    )
    run_dir = state_root / "runs" / repo.name / run_id
    worktree = state_root / "worktrees" / repo.name / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    # The journal opens with the run directory and closes on the decision:
    # every transition is on the file system before the run acts on it.
    journal = Journal(run_dir / JOURNAL_NAME, run_id)
    journal.record(
        "run.started",
        {"base_commit": base_commit, "max_iterations": request.max_iterations},
    )

    (run_dir / "TASK.md").write_text(task.raw_text, encoding="utf-8")
    (run_dir / "constitution.toml").write_text(
        constitution.raw_text, encoding="utf-8"
    )
    # The catalogue rates every token of the run, so the run keeps the copy
    # it was rated by, whatever the package publishes later.
    (run_dir / "catalogue.toml").write_text(catalogue.raw_text, encoding="utf-8")
    sensor_paths, sensor_evidence = freeze_sensors(state_root, run_dir, constitution)
    journal.record(
        "inputs.frozen",
        {
            "task_sha256": sha256_text(task.raw_text),
            "constitution_sha256": sha256_text(constitution.raw_text),
            "catalogue_sha256": sha256_text(catalogue.raw_text),
            "sensors": sorted(sensor_evidence),
        },
    )
    journal.record(
        "inference.profiles_frozen",
        {role: profile.requested.to_document() for role, profile in roles(inference)},
    )

    profiles = confinement(
        repo=repo,
        worktree=worktree,
        run_dir=run_dir,
        state_root=state_root,
        git_dir=common_git_dir(repo),
        execution=constitution.execution,
        provider=provider,
    )

    context = RunContext(
        request=request,
        repo=repo,
        task=task,
        constitution=constitution,
        catalogue=catalogue,
        provider=provider,
        base_commit=base_commit,
        run_id=run_id,
        state_root=state_root,
        run_dir=run_dir,
        worktree=worktree,
        implementer=implementer,
        reviewer=reviewer,
        confinement=profiles,
        sensor_paths=sensor_paths,
        sensor_evidence=sensor_evidence,
    )
    skeleton = Evidence(
        schema_version=EVIDENCE_SCHEMA_VERSION,
        run_id=run_id,
        started_at=utc_now(),
        repo=str(repo),
        state_dir=str(state_root),
        base_commit=base_commit,
        task_sha256=sha256_text(task.raw_text),
        constitution_sha256=sha256_text(constitution.raw_text),
        catalogue_sha256=sha256_text(catalogue.raw_text),
        runtime=runtime_metadata(
            implementer, reviewer, request.model, request.review_model
        ),
        inference=inference,
        sensors=sensor_evidence,
        environment=declared_environment(constitution),
        actuator_isolation=implementer.describe_isolation(profiles.actuator),
        gate_isolation=profiles.gate_evidence(),
        status=RunStatus.RUNNING,
        iterations=(),
        decision=Decision(reasons=()),
        run_dir=str(run_dir),
        worktree=None,
        events=journal.summary(),
    )
    record = RunRecord(run_dir=run_dir, journal=journal, document=skeleton)
    record.persist()
    return context, record
