"""The shape of `evidence.json`, declared once.

A run writes one document, and `schema_version` says which shape it is. That
number is a promise to every reader of a run directory — the verification
command, a later comparison, a protocol report — so what the fields are is
stated here rather than left to whichever phase happens to fill one.

Blocks filled in a single place are typed at their producer, so a field that
is omitted, misnamed or given the wrong kind of value is refused where it is
written. Blocks a run fills in stages are reached through `RunRecord`, which
names them.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from ..actuators.base import Actuation, ObservedProfile, ReviewMeta
from ..actuators.inventory import ProfileVerdict
from ..evidence.journal import EventsSummary
from ..runtime.sandbox import IsolationEvidence
from ..sensors.gates import GateResult


class FileRecord(TypedDict):
    """One artefact the run wrote, and what it digests to."""

    path: str
    sha256: str


class Feedback(FileRecord):
    """What the controller told the actuator, kept beside its digest."""

    text: str


class FrozenSensor(FileRecord):
    """One external sensor as the run froze it, and what it was asked for."""

    reference: str


class RuntimeMetadata(TypedDict):
    """What ran this run: the controller, both backends, and the host tooling."""

    codeservo_version: str
    codeservo_commit: str
    actuator: str
    actuator_version: str
    review_actuator: str
    review_actuator_version: str
    implementer_model: str
    reviewer_model: str
    python_version: str
    git_version: str


class RequestedProfile(TypedDict):
    """One role's inference profile, as the run resolved the request."""

    backend: str
    model: str | None
    effort: str | None
    speed: str


class InferenceProfile(TypedDict):
    """One role's profile: asked for, checked, then reported by the backend."""

    requested: RequestedProfile
    validation: ProfileVerdict
    native: dict[str, Any] | None
    observed: ObservedProfile
    provenance: dict[str, str]


class ReportedBlock(TypedDict):
    """What a backend answered, and what is said about each field of it."""

    observed: ObservedProfile
    provenance: dict[str, str]


class Inference(TypedDict):
    """The two roles, each an independent control input."""

    implementer: InferenceProfile
    reviewer: InferenceProfile


class GateIsolation(TypedDict):
    """The confinement each measured tree was measured under."""

    source: IsolationEvidence
    candidate: IsolationEvidence


class CandidateDigests(TypedDict):
    """The three provider files of the candidate, as they are right now."""

    manifest_sha256: str | None
    lock_sha256: str | None
    config_sha256: str | None


class CandidateEnvironment(CandidateDigests):
    """What installing the declared environment into the candidate did."""

    prefix_path: str
    command: list[str]
    exit_code: int
    duration_ms: int
    unchanged_at_end: bool


class ResolvedEnvironment(TypedDict):
    """What the lockfile resolves to, and the tasks the environment declares."""

    provider_version: str
    platform: str
    declared_tasks: list[str]
    packages_path: str
    packages_sha256: str
    package_count: int


class EnvironmentBlock(TypedDict, total=False):
    """The execution environment block, filled as the run establishes it.

    A run declaring no provider carries the provider alone and says `none`;
    nothing else is asserted about an environment nobody declared.
    """

    provider: str
    manifest_path: str
    manifest_sha256: str
    lock_path: str
    lock_sha256: str
    environment: str
    provider_version: str
    platform: str
    declared_tasks: list[str]
    packages_path: str
    packages_sha256: str
    package_count: int
    candidate: CandidateEnvironment


class ScopeResult(TypedDict):
    """What the structural invariants saw in the candidate's diff."""

    passed: bool
    summary: str
    details: dict[str, Any]


class Iteration(TypedDict):
    """One turn of the feedback loop, as far as it got.

    Everything after the prompt is optional, because an iteration that ended
    on a refused actuation or a broken sensor holds what happened up to that
    point and nothing about what never ran.
    """

    iteration: int
    feedback_received: str
    input_state: FileRecord
    prompt: NotRequired[FileRecord]
    agent_error: NotRequired[str]
    agent: NotRequired[Actuation]
    actuator_state: NotRequired[FileRecord]
    scope: NotRequired[ScopeResult]
    quick_gates: NotRequired[list[GateResult]]
    observed_state: NotRequired[FileRecord]
    controller_feedback: NotRequired[Feedback | None]


class ReviewBlock(TypedDict):
    """What the reviewer was given, and what it answered.

    The first four are recorded before it starts, so a reviewer that fails
    cannot erase the observations it was handed.
    """

    prompt: FileRecord
    observations: dict[str, Any]
    observations_sha256: str
    isolation: IsolationEvidence
    result: NotRequired[dict[str, Any]]
    result_sha256: NotRequired[str]
    meta: NotRequired[ReviewMeta]


class Decision(TypedDict):
    """Why the run ended where it did. An accepted run gives no reason."""

    reasons: list[str]


class Evidence(TypedDict, total=False):
    """The record of one run.

    The fields a run has before it measures anything are always there. The
    rest appear where the run reached them, so a record never asserts a phase
    that never ran.
    """

    schema_version: int
    run_id: str
    started_at: str
    finished_at: str
    repo: str
    state_dir: str
    base_commit: str
    task_sha256: str
    constitution_sha256: str
    runtime: RuntimeMetadata
    inference: Inference
    sensors: dict[str, FrozenSensor]
    environment: EnvironmentBlock
    actuator_isolation: IsolationEvidence
    gate_isolation: GateIsolation
    status: str
    iterations: list[Iteration]
    baseline: list[GateResult]
    full_gates: list[GateResult]
    full_gate_state: FileRecord
    review: ReviewBlock
    decision: Decision
    patch_sha256: str | None
    run_dir: str
    worktree: str | None
    events: EventsSummary
