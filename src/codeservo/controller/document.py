"""The shape of `evidence.json`, declared once.

A run writes one document, and `schema_version` says which shape it is. That
number is a promise to every reader of a run directory — the verification
command, a later comparison, a protocol report — so what the fields are is
stated here rather than left to whichever phase happens to fill one.

Every block is frozen, the record included. A run does not edit what it has
already stated: each transition builds the record it reached from the one
before it, and a field the run never measured carries `UNSET` and is absent
from what is written rather than present as null.

Blocks filled in a single place are typed at their producer, so a field that
is omitted, misnamed or given the wrong kind of value is refused where it is
written.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from ..actuators.base import Actuation, ObservedProfile, ReviewMeta, Tokens
from ..actuators.catalogue import Backend, Effort
from ..domain.document import UNSET, Document, Unset
from ..domain.run import RunStatus
from ..evidence.journal import EventsSummary
from ..runtime.sandbox import IsolationEvidence
from ..sensors.gates import GateResult


@dataclass(frozen=True, kw_only=True)
class FileRecord(Document):
    """One artefact the run wrote, and what it digests to."""

    path: str
    sha256: str


@dataclass(frozen=True, kw_only=True)
class Feedback(FileRecord):
    """What the controller told the actuator, kept beside its digest."""

    text: str


@dataclass(frozen=True, kw_only=True)
class FrozenSensor(FileRecord):
    """One external sensor as the run froze it, and what it was asked for."""

    reference: str


@dataclass(frozen=True, kw_only=True)
class RuntimeMetadata(Document):
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


@dataclass(frozen=True, kw_only=True)
class RequestedProfile(Document):
    """One role's inference profile, as the run resolved the request.

    Every field is named: the model is one the catalogue lists for that
    backend and the effort one of the four it names, both settled before the
    run directory exists.
    """

    backend: Backend
    model: str
    effort: Effort


@dataclass(frozen=True, kw_only=True)
class InferenceProfile(Document):
    """One role's profile: asked for, then reported by the backend.

    The request is frozen before anything actuates. What the backend then
    reported replaces whatever an earlier call reported, so the block
    describes the last actuation of that role and never a mixture.
    """

    requested: RequestedProfile
    native: dict[str, Any] | None
    observed: ObservedProfile
    provenance: dict[str, str]


class PriceBasis(StrEnum):
    """Which model a billed block was rated at, and why that one.

    A backend that names the model it billed is rated at that model. One that
    names none is rated at the model the run requested, the only model it
    could have run, and the record says the attribution is the controller's.
    """

    REPORTED_MODEL = "reported_model"
    REQUESTED_MODEL = "requested_model"


@dataclass(frozen=True, kw_only=True)
class PricedUsage(Document):
    """One billed block, rated at the catalogue's list price for its model.

    `cost_usd` is the controller's arithmetic and stays empty where the
    catalogue cannot rate the block. `reported_cost_usd` is what the backend
    itself said, kept beside it and never merged with it.
    """

    model: str
    basis: PriceBasis
    tokens: Tokens
    cost_usd: float | None
    reported_cost_usd: float | None


@dataclass(frozen=True, kw_only=True)
class Consumption(Document):
    """What one call consumed, and what it cost at list price.

    The total is the sum of the blocks when every block was rated, and empty
    otherwise: a sum over part of a session would read as the cost of all of
    it.
    """

    items: tuple[PricedUsage, ...]
    cost_usd: float | None


@dataclass(frozen=True, kw_only=True)
class Inference(Document):
    """The two roles, each an independent control input."""

    implementer: InferenceProfile
    reviewer: InferenceProfile


@dataclass(frozen=True, kw_only=True)
class GateIsolation(Document):
    """The confinement each measured tree was measured under."""

    source: IsolationEvidence
    candidate: IsolationEvidence


@dataclass(frozen=True, kw_only=True)
class CandidateDigests(Document):
    """The three provider files of the candidate, as they are right now."""

    manifest_sha256: str | None
    lock_sha256: str | None
    config_sha256: str | None


@dataclass(frozen=True, kw_only=True)
class CandidateEnvironment(CandidateDigests):
    """What installing the declared environment into the candidate did.

    The verdict is unset until the digests above are taken a second time:
    whether the workspace still holds what was installed into it is what a
    recomputation answers, and a block carrying it before one ran would state
    a comparison nobody made.
    """

    prefix_path: str
    command: tuple[str, ...]
    exit_code: int
    duration_ms: int
    unchanged_at_end: bool | Unset = UNSET


@dataclass(frozen=True, kw_only=True)
class ResolvedEnvironment(Document):
    """What the lockfile resolves to, and the tasks the environment declares."""

    provider_version: str
    platform: str
    declared_tasks: tuple[str, ...]
    packages_path: str
    packages_sha256: str
    package_count: int


@dataclass(frozen=True, kw_only=True)
class EnvironmentBlock(Document):
    """The execution environment block, filled as the run establishes it.

    No field carries a value before the reading that establishes it, and none
    carries a default standing in for one. The provider is what the
    constitution declares, or `none` where it declares no execution table;
    everything else — the candidate's verdict included — stays unset until
    something has read it, whichever step of the run does the reading.
    """

    provider: str
    manifest_path: str | Unset = UNSET
    manifest_sha256: str | Unset = UNSET
    lock_path: str | Unset = UNSET
    lock_sha256: str | Unset = UNSET
    environment: str | Unset = UNSET
    provider_version: str | Unset = UNSET
    platform: str | Unset = UNSET
    declared_tasks: tuple[str, ...] | Unset = UNSET
    packages_path: str | Unset = UNSET
    packages_sha256: str | Unset = UNSET
    package_count: int | Unset = UNSET
    candidate: CandidateEnvironment | Unset = UNSET

    def resolving(self, resolved: ResolvedEnvironment) -> EnvironmentBlock:
        """This block, once the lockfile has been resolved to an inventory.

        The six fields are named here rather than merged from whatever the
        resolution happened to return, so the block carries what it declares
        and a resolution gaining a field cannot widen the record silently.
        """
        return replace(
            self,
            provider_version=resolved.provider_version,
            platform=resolved.platform,
            declared_tasks=resolved.declared_tasks,
            packages_path=resolved.packages_path,
            packages_sha256=resolved.packages_sha256,
            package_count=resolved.package_count,
        )


@dataclass(frozen=True, kw_only=True)
class ScopeResult(Document):
    """What the structural invariants saw in the candidate's diff."""

    passed: bool
    summary: str
    details: dict[str, Any]


@dataclass(frozen=True, kw_only=True)
class ReviewBlock(Document):
    """What the reviewer was given, and what it answered.

    The first four are recorded before it starts, so a reviewer that fails
    cannot erase the observations it was handed.
    """

    prompt: FileRecord
    observations: dict[str, Any]
    observations_sha256: str
    isolation: IsolationEvidence
    result: dict[str, Any] | Unset = UNSET
    result_sha256: str | Unset = UNSET
    meta: ReviewMeta | Unset = UNSET
    consumption: Consumption | Unset = UNSET


@dataclass(frozen=True, kw_only=True)
class Iteration(Document):
    """One turn of the feedback loop, as far as it got.

    Everything after the prompt is unset until the iteration reaches it,
    because an iteration that ended on a refused actuation or a broken sensor
    holds what happened up to that point and nothing about what never ran.
    The full gates and the review belong to the iteration whose candidate
    they measured: an iteration that failed a quick gate never reaches them,
    and one that reached them and was not accepted is followed by another.
    """

    iteration: int
    feedback_received: str
    input_state: FileRecord
    prompt: FileRecord | Unset = UNSET
    agent_error: str | Unset = UNSET
    agent: Actuation | Unset = UNSET
    consumption: Consumption | Unset = UNSET
    actuator_state: FileRecord | Unset = UNSET
    scope: ScopeResult | Unset = UNSET
    quick_gates: tuple[GateResult, ...] | Unset = UNSET
    observed_state: FileRecord | Unset = UNSET
    full_gates: tuple[GateResult, ...] | Unset = UNSET
    full_gate_state: FileRecord | Unset = UNSET
    review: ReviewBlock | Unset = UNSET
    controller_feedback: Feedback | None | Unset = UNSET


@dataclass(frozen=True, kw_only=True)
class Decision(Document):
    """Why the run ended where it did. An accepted run gives no reason."""

    reasons: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class Evidence(Document):
    """The record of one run.

    The fields a run has before it measures anything are always there. The
    rest stay unset until the run reaches them, so a record never asserts a
    phase that never ran.
    """

    schema_version: int
    run_id: str
    started_at: str
    repo: str
    state_dir: str
    base_commit: str
    task_sha256: str
    constitution_sha256: str
    catalogue_sha256: str
    runtime: RuntimeMetadata
    inference: Inference
    sensors: dict[str, FrozenSensor]
    environment: EnvironmentBlock
    actuator_isolation: IsolationEvidence
    gate_isolation: GateIsolation
    status: RunStatus
    iterations: tuple[Iteration, ...]
    decision: Decision
    run_dir: str
    worktree: str | None
    events: EventsSummary
    finished_at: str | Unset = UNSET
    baseline: tuple[GateResult, ...] | Unset = UNSET
    patch_sha256: str | None | Unset = UNSET
