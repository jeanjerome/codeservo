from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, pixi
from .actuator import Actuator, ActuatorError, default_actuator_name, load_actuator
from .config import load_constitution
from .evidence import (
    relative_evidence_paths,
    sha256_file,
    sha256_json,
    sha256_path,
    sha256_text,
    write_json,
)
from .gates import baseline_gates, run_gates
from .git import (
    common_git_dir,
    create_worktree,
    head,
    is_clean,
    make_patch,
    root,
    scope_sensor,
)
from .model import Constitution, ExecutionEnvironment
from .models import DEFAULT_SPEED, PROFILE_UNSUPPORTED, validate_profile
from .process import tail
from .prompts import implementer_prompt, reviewer_prompt
from .sandbox import Isolation, SandboxError, isolation_evidence
from .task import load_task


class ControlFailure(RuntimeError):
    pass


# The shape of evidence.json. The observation bundle versions its own shape.
EVIDENCE_SCHEMA_VERSION = 12

# A run that declares no execution provider measures through whatever the host
# offers, and says so.
NO_ENVIRONMENT = {"provider": "none"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _command_version(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    # A command that failed reports a diagnostic, not the value asked for.
    if completed.returncode != 0:
        return "unavailable"
    output = completed.stdout.strip() or completed.stderr.strip()
    return output.splitlines()[0] if output else "unavailable"


def _runtime_metadata(
    actuator: Actuator,
    reviewer: Actuator,
    model: str | None,
    review_model: str | None,
) -> dict:
    """Name the two backends a run drives, and the CLI each one answered with.

    Both roles are named even when a single backend serves them, so a record
    never leaves the reviewing backend to be inferred from the implementing one.
    """
    source_root = Path(__file__).resolve().parents[2]
    actuator_version = _command_version(list(actuator.version_command))
    return {
        "codeservo_version": __version__,
        "codeservo_commit": _command_version(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"]
        ),
        "actuator": actuator.name,
        "actuator_version": actuator_version,
        "review_actuator": reviewer.name,
        "review_actuator_version": (
            actuator_version
            if reviewer.version_command == actuator.version_command
            else _command_version(list(reviewer.version_command))
        ),
        "implementer_model": model or f"{actuator.name}-default",
        "reviewer_model": review_model or f"{reviewer.name}-default",
        "python_version": platform.python_version(),
        "git_version": _command_version(["git", "--version"]),
    }


def _profile(
    backend: str, model: str | None, effort: str | None, speed: str
) -> dict:
    """Freeze one requested inference profile before anything actuates.

    The request is recorded as it was resolved, next to what the local
    inventory of that same backend can say about it. Nothing the backend
    answers is filled in here, so a substitution can never be read back as the
    configuration asked for.
    """
    return {
        "requested": {
            "backend": backend,
            "model": model,
            "effort": effort,
            "speed": speed,
        },
        "validation": validate_profile(
            backend=backend, model=model, effort=effort, speed=speed
        ),
        "native": None,
        "observed": {"model": None, "effort": None, "speed": None},
        "provenance": "incomplete",
    }


def _inference(*, implementer: dict, reviewer: dict) -> dict:
    """Freeze the two requested inference profiles of a run.

    The roles are independent control inputs: each is checked against the
    inventory of its own backend, so one backend's cache never answers for the
    other's.
    """
    return {
        "implementer": _profile(**implementer),
        "reviewer": _profile(**reviewer),
    }


def _record_actuation(profile: dict, agent: dict) -> None:
    """Keep the profile of the last actuation, replacing any earlier one."""
    observed = agent["observed"]
    profile["native"] = agent["native"]
    profile["observed"] = observed
    profile["provenance"] = "complete" if observed["model"] else "incomplete"


def _resolve_state_dir(repo: Path, state_dir: Path | None) -> Path:
    root_dir = (
        state_dir.expanduser().resolve()
        if state_dir is not None
        else (Path.home() / ".codeservo").resolve()
    )
    if root_dir == repo or root_dir.is_relative_to(repo):
        raise ControlFailure("state directory must be outside the target repository")
    return root_dir


def _write_patch_snapshot(path: Path, worktree: Path, base_commit: str) -> dict:
    patch = make_patch(worktree, base_commit)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(patch, encoding="utf-8")
    return {
        "path": str(path),
        "sha256": sha256_text(patch),
    }


def _freeze_sensors(
    state_root: Path, run_dir: Path, constitution: Constitution
) -> tuple[dict[str, Path], dict[str, dict]]:
    sensor_root = (state_root / "sensors").resolve()
    paths: dict[str, Path] = {}
    evidence: dict[str, dict] = {}
    for gate in constitution.gates:
        if gate.sensor is None:
            continue
        reference = Path(gate.sensor)
        unresolved_source = sensor_root / reference
        source = unresolved_source.resolve()
        if (
            reference == Path(".")
            or reference.is_absolute()
            or not source.is_relative_to(sensor_root)
        ):
            raise ControlFailure(
                f"gate {gate.name}: sensor must stay under {sensor_root}"
            )
        if not source.exists():
            raise ControlFailure(f"gate {gate.name}: missing external sensor {source}")
        lexical_sources = (unresolved_source, *unresolved_source.parents)
        if any(
            path.is_relative_to(sensor_root) and path.is_symlink()
            for path in lexical_sources
        ) or any(path.is_symlink() for path in source.rglob("*")):
            raise ControlFailure(
                f"gate {gate.name}: sensor cannot contain symbolic links"
            )

        target = run_dir / "sensors" / gate.name
        if source.is_dir():
            shutil.copytree(
                source,
                target,
                ignore=shutil.ignore_patterns(
                    "__pycache__", ".pytest_cache", "*.pyc", ".DS_Store"
                ),
            )
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        paths[gate.name] = target
        evidence[gate.name] = {
            "path": str(target),
            "reference": gate.sensor,
            "sha256": sha256_path(target),
        }
    return paths, evidence


def _committed_sha256(repo: Path, commit: str, relative: str) -> str:
    """The digest of one file as the base commit holds it.

    The frozen control input is the source repository at that commit, not a
    working tree a later step could still touch.
    """
    completed = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "blob", f"{commit}:{relative}"],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ControlFailure(
            f"execution environment: {relative} is not committed at {commit}"
        )
    return hashlib.sha256(completed.stdout).hexdigest()


def _frozen_environment(
    repo: Path, base_commit: str, execution: ExecutionEnvironment
) -> dict:
    """The declaration and the two digests, before any provider command runs."""
    return {
        "provider": execution.provider,
        "manifest_path": execution.manifest,
        "manifest_sha256": _committed_sha256(repo, base_commit, execution.manifest),
        "lock_path": execution.lock,
        "lock_sha256": _committed_sha256(repo, base_commit, execution.lock),
        "environment": execution.environment,
    }


def _resolved_environment(
    repo: Path,
    run_dir: Path,
    execution: ExecutionEnvironment,
    tasks: tuple[str, ...],
) -> tuple[dict, str]:
    """What the lockfile resolves to, and the tasks the environment declares.

    The inventory is stored under the run record, so the packages a
    measurement ran against stay readable from the evidence alone. The
    directory the provider reports for this tree is returned next to it and
    never recorded: it is the operator's location, not a fact about the run.
    """
    resolved = pixi.freeze(
        manifest=repo / execution.manifest,
        lock_path=execution.lock,
        environment=execution.environment,
        tasks=tasks,
    )
    relative = "environment/packages.json"
    packages_path = run_dir / relative
    write_json(packages_path, resolved.packages)
    record = {
        "provider_version": resolved.version,
        "platform": resolved.platform,
        "declared_tasks": list(resolved.tasks),
        "packages_path": relative,
        "packages_sha256": sha256_file(packages_path),
        "package_count": len(resolved.packages),
    }
    return record, resolved.prefix


def _optional_sha256(path: Path) -> str | None:
    """The digest of a file, or null where there is no file."""
    return sha256_file(path) if path.is_file() else None


def _candidate_digests(worktree: Path, execution: ExecutionEnvironment) -> dict:
    """The three provider files of the candidate, as they are right now.

    A file that is gone digests to null, so a deleted manifest, lockfile or
    configuration reads as a change rather than as an unreadable record.
    """
    manifest = worktree / execution.manifest
    return {
        "manifest_sha256": _optional_sha256(manifest),
        "lock_sha256": _optional_sha256(worktree / execution.lock),
        "config_sha256": _optional_sha256(pixi.config_path(manifest)),
    }


def _prepare_candidate(
    worktree: Path, execution: ExecutionEnvironment
) -> tuple[dict, str]:
    """Install the declared environment into the isolated checkout.

    The candidate is the only tree the controller prepares. The digests are
    taken after the installation, so they describe the workspace every later
    measurement runs against, and are what each recomputation compares to.
    """
    installation = pixi.install(
        manifest=worktree / execution.manifest, environment=execution.environment
    )
    record = {
        "prefix_path": installation.prefix_path,
        "command": list(installation.command),
        "exit_code": installation.exit_code,
        "duration_ms": installation.duration_ms,
        **_candidate_digests(worktree, execution),
        "unchanged_at_end": True,
    }
    return record, installation.diagnostic


def _changed_environment(
    environment: dict, worktree: Path, execution: ExecutionEnvironment | None
) -> list[str]:
    """Provider files of the candidate that moved since it was prepared.

    Every measurement runs under variables forbidding it to resolve or
    install, so a manifest, lockfile or provider configuration that differs
    from what was prepared is a control failure of the run and not a failing
    gate: what was frozen is no longer what was measured.
    """
    candidate = environment.get("candidate")
    if execution is None or candidate is None:
        return []
    named = {
        "manifest_sha256": execution.manifest,
        "lock_sha256": execution.lock,
        "config_sha256": pixi.config_path(Path(execution.manifest)).as_posix(),
    }
    current = _candidate_digests(worktree, execution)
    reasons = [
        f"execution environment: {named[field]} changed during the run"
        for field, digest in current.items()
        if digest != candidate[field]
    ]
    candidate["unchanged_at_end"] = not reasons
    return reasons


def _review_schema_path(source_root: Path | None = None) -> Path:
    """Locate the frozen review schema.

    An installed wheel carries no repository-level `templates/`, so the package
    keeps its own copy of the schema next to the module.
    """
    root = source_root if source_root is not None else Path(__file__).resolve().parents[2]
    repository_copy = root / "templates" / "review.schema.json"
    if repository_copy.is_file():
        return repository_copy
    return Path(__file__).with_name("review.schema.json")


def _altered_sensors(
    sensor_paths: dict[str, Path], sensor_evidence: dict[str, dict]
) -> list[str]:
    """Frozen sensors whose content changed after the controller froze them."""
    return sorted(
        name
        for name, path in sensor_paths.items()
        if sha256_path(path) != sensor_evidence[name]["sha256"]
    )


def _observed_tail(path: str, locations: tuple[Path, ...]) -> str:
    """Bounded gate output with controller-owned locations removed.

    The reviewer is told what a gate emitted, never where the controller keeps
    the record or the candidate.
    """
    text = tail(path)
    # Longest first, so a location nested in another is redacted whole.
    for location in sorted(locations, key=lambda item: len(str(item)), reverse=True):
        text = text.replace(str(location), "<redacted>")
    return text


def _observations(
    constitution: Constitution,
    quick: list[dict],
    full: list[dict],
    locations: tuple[Path, ...],
) -> dict:
    """The successful gate measurements handed to the read-only reviewer.

    Classification comes from the frozen constitution, so a repository gate
    cannot present itself as an external acceptance sensor by naming itself one.
    """
    sensors = {gate.name: gate.sensor for gate in constitution.gates}
    gates: list[dict] = []
    for phase, results in (("quick", quick), ("full", full)):
        for result in results:
            sensor = sensors.get(result["name"])
            gates.append(
                {
                    "phase": phase,
                    "name": result["name"],
                    "kind": "repository_gate" if sensor is None else "external_sensor",
                    "sensor": sensor,
                    "passed": result["passed"],
                    "exit_code": result["exit_code"],
                    "timed_out": result["timed_out"],
                    "duration_ms": result["duration_ms"],
                    "stdout_sha256": result["stdout_sha256"],
                    "stderr_sha256": result["stderr_sha256"],
                    "result_sha256": result["result_sha256"],
                    "stdout_tail": _observed_tail(result["stdout_path"], locations),
                    "stderr_tail": _observed_tail(result["stderr_path"], locations),
                }
            )
    return {"schema_version": 1, "gates": gates}


def _gate_feedback(results: list[dict]) -> str:
    chunks: list[str] = []
    for result in results:
        if result["passed"]:
            continue
        chunks.append(
            "\n".join(
                [
                    f"Gate {result['name']} FAILED",
                    f"Command: {result['command']}",
                    f"Exit code: {result['exit_code']}",
                    "stdout (tail):",
                    tail(result["stdout_path"]),
                    "stderr (tail):",
                    tail(result["stderr_path"]),
                ]
            )
        )
    return "\n\n".join(chunks)


def _review_decision(review: dict, task_criteria: dict[str, str], blocking: tuple[str, ...]) -> list[str]:
    reasons: list[str] = []
    seen: dict[str, str] = {}
    for item in review.get("criteria", []):
        criterion_id = str(item.get("id", ""))
        status = str(item.get("status", ""))
        if criterion_id in seen:
            reasons.append(f"review duplicated criterion {criterion_id}")
        seen[criterion_id] = status

    for criterion_id in task_criteria:
        status = seen.get(criterion_id)
        if status is None:
            reasons.append(f"review missing criterion {criterion_id}")
        elif status != "satisfied":
            reasons.append(f"criterion {criterion_id} is {status}")

    extras = sorted(set(seen) - set(task_criteria))
    for extra in extras:
        reasons.append(f"review returned unknown criterion {extra}")

    blocking_set = set(blocking)
    for finding in review.get("findings", []):
        severity = str(finding.get("severity", ""))
        if severity in blocking_set:
            message = str(finding.get("message", "blocking review finding"))
            reasons.append(f"{severity} finding: {message}")
    return reasons


def run(
    *,
    repo_path: Path,
    task_path: Path,
    max_iterations: int = 4,
    model: str | None = None,
    review_model: str | None = None,
    agent_timeout_seconds: int = 1800,
    state_dir: Path | None = None,
    actuator: str | None = None,
    effort: str | None = None,
    speed: str = DEFAULT_SPEED,
    review_actuator: str | None = None,
    review_effort: str | None = None,
    review_speed: str = DEFAULT_SPEED,
) -> dict:
    backend = load_actuator(actuator or default_actuator_name())
    # The reviewer backend is loaded on its own, so a run can implement with
    # one command-line tool and decide with another. Asking for neither leaves
    # the implementer's backend serving both roles.
    review_backend = load_actuator(review_actuator or backend.name)
    inference = _inference(
        implementer={
            "backend": backend.name,
            "model": model,
            "effort": effort,
            "speed": speed,
        },
        reviewer={
            "backend": review_backend.name,
            "model": review_model,
            "effort": review_effort,
            "speed": review_speed,
        },
    )
    repo = root(repo_path)
    task = load_task(task_path.resolve())
    constitution = load_constitution(repo)
    base_commit = head(repo)
    run_id = _run_id()
    state_root = _resolve_state_dir(repo, state_dir)
    run_dir = state_root / "runs" / repo.name / run_id
    worktree = state_root / "worktrees" / repo.name / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    frozen_task = run_dir / "TASK.md"
    frozen_constitution = run_dir / "constitution.toml"
    frozen_task.write_text(task.raw_text, encoding="utf-8")
    frozen_constitution.write_text(constitution.raw_text, encoding="utf-8")
    sensor_paths, sensor_evidence = _freeze_sensors(state_root, run_dir, constitution)
    isolation = Isolation(
        denied=(
            state_root / "runs",
            state_root / "sensors",
            state_root / ".git",
            common_git_dir(repo),
        ),
        read_only=(repo,),
    )
    # Gates are controller-owned measurements: they read the frozen sensors and
    # write nothing into the record they produce.
    gate_isolation = Isolation(read_only=(run_dir,))

    evidence: dict = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "run_id": run_id,
        "started_at": _now(),
        "repo": str(repo),
        "state_dir": str(state_root),
        "base_commit": base_commit,
        "task_sha256": sha256_text(task.raw_text),
        "constitution_sha256": sha256_text(constitution.raw_text),
        "runtime": _runtime_metadata(backend, review_backend, model, review_model),
        "inference": inference,
        "sensors": sensor_evidence,
        "environment": dict(NO_ENVIRONMENT),
        "actuator_isolation": backend.describe_isolation(isolation),
        "gate_isolation": isolation_evidence(gate_isolation, "macos-sandbox-exec"),
        "status": "RUNNING",
        "iterations": [],
        "decision": {"reasons": []},
        "run_dir": str(run_dir),
        "worktree": None,
    }
    evidence_path = run_dir / "evidence.json"

    def persist() -> None:
        write_json(evidence_path, relative_evidence_paths(evidence, run_dir))

    persist()

    def finish(status: str, reasons: list[str]) -> dict:
        patch = ""
        if worktree.exists():
            patch = make_patch(worktree, base_commit)
            (run_dir / "change.patch").write_text(patch, encoding="utf-8")
        evidence["status"] = status
        evidence["finished_at"] = _now()
        evidence["decision"] = {"reasons": reasons}
        evidence["patch_sha256"] = sha256_text(patch) if patch else None
        evidence["run_dir"] = str(run_dir)
        evidence["worktree"] = str(worktree) if worktree.exists() else None
        persist()
        return evidence

    # Each profile is a control input, so a request the inventory of its own
    # backend contradicts ends the run here: no checkout and no agent process
    # ever exists for it.
    contradicted = [
        f"configuration error: {role} profile: {profile['validation']['reason']}"
        for role, profile in inference.items()
        if profile["validation"]["status"] == PROFILE_UNSUPPORTED
    ]
    if contradicted:
        return finish("REJECTED", contradicted)

    if not is_clean(repo):
        return finish("REJECTED", ["source repository is not clean"])

    # The environment is a control input, so it is frozen and resolved before
    # anything is measured: a lockfile that disagrees with the manifest, an
    # environment that does not exist, or a task no environment declares ends
    # the run here, with no checkout and no gate ever running.
    if constitution.execution is not None:
        declared_tasks = tuple(
            gate.task for gate in constitution.gates if gate.task is not None
        )
        try:
            evidence["environment"] = _frozen_environment(
                repo, base_commit, constitution.execution
            )
            persist()
            resolved, source_prefix = _resolved_environment(
                repo, run_dir, constitution.execution, declared_tasks
            )
            evidence["environment"].update(resolved)
        except (ControlFailure, pixi.ProviderError) as exc:
            return finish("REJECTED", [str(exc)])
        persist()

        # The source repository is the operator's tree: the controller prepares
        # the candidate and never this one, and writes nothing here. A baseline
        # gate that measures through the provider therefore needs an
        # environment that is already installed, and the run says so rather
        # than creating one.
        measured_at_source = any(
            gate.task is not None for gate in baseline_gates(constitution)
        )
        if measured_at_source and not Path(source_prefix).is_dir():
            return finish(
                "REJECTED",
                [
                    "execution environment: environment"
                    f" {constitution.execution.environment} is not installed in"
                    f" the source repository: {source_prefix} does not exist"
                ],
            )

    baseline = run_gates(
        repo=repo,
        gates=baseline_gates(constitution),
        out_dir=run_dir / "baseline",
        isolation=gate_isolation,
        execution=constitution.execution,
    )
    evidence["baseline"] = baseline
    persist()
    if not all(g["passed"] for g in baseline):
        return finish("REJECTED", ["baseline gate failed"])
    if not is_clean(repo):
        return finish("REJECTED", ["baseline gate mutated the source repository"])

    create_worktree(repo, worktree, base_commit)
    evidence["worktree"] = str(worktree)
    persist()

    # The candidate is prepared once the checkout exists and before anything
    # actuates in it, so the first measurement already runs on the environment
    # the lockfile pins instead of on whatever the host happens to offer.
    if constitution.execution is not None:
        try:
            candidate, diagnostic = _prepare_candidate(worktree, constitution.execution)
        except pixi.ProviderError as exc:
            return finish("REJECTED", [str(exc)])
        evidence["environment"]["candidate"] = candidate
        persist()
        environment_name = constitution.execution.environment
        if candidate["exit_code"] != 0:
            return finish(
                "REJECTED",
                [
                    f"execution environment: installing {environment_name} into"
                    f" the candidate failed: {diagnostic}"
                ],
            )
        if not Path(candidate["prefix_path"]).is_dir():
            return finish(
                "REJECTED",
                [
                    f"execution environment: installing {environment_name}"
                    f" created no environment at {candidate['prefix_path']}"
                ],
            )

    feedback = ""
    accepted_quick: list[dict] | None = None

    for iteration in range(1, max_iterations + 1):
        iteration_dir = run_dir / "iterations" / f"{iteration:02d}"
        input_state = _write_patch_snapshot(
            iteration_dir / "input.patch", worktree, base_commit
        )
        prompt = implementer_prompt(task, constitution, feedback)
        prompt_path = iteration_dir / "prompt.md"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        record = {
            "iteration": iteration,
            "feedback_received": feedback,
            "input_state": input_state,
            "prompt": {
                "path": str(prompt_path),
                "sha256": sha256_text(prompt),
            },
        }
        try:
            agent = backend.implement(
                worktree=worktree,
                prompt=prompt,
                out_dir=iteration_dir / "agent",
                model=model,
                timeout_seconds=agent_timeout_seconds,
                isolation=isolation,
                effort=effort,
                speed=speed,
            )
        except (ActuatorError, SandboxError) as exc:
            record["agent_error"] = str(exc)
            evidence["iterations"].append(record)
            persist()
            return finish("REJECTED", [str(exc)])

        record["agent"] = agent
        _record_actuation(inference["implementer"], agent)
        record["actuator_state"] = _write_patch_snapshot(
            iteration_dir / "actuator.patch", worktree, base_commit
        )
        if agent["exit_code"] != 0:
            evidence["iterations"].append(record)
            persist()
            return finish("REJECTED", [f"implementer exited with {agent['exit_code']}"])

        scope = scope_sensor(worktree, base_commit, constitution.scope)
        quick = run_gates(
            repo=worktree,
            gates=constitution.gates_for("quick"),
            out_dir=iteration_dir / "quick",
            sensor_paths=sensor_paths,
            isolation=gate_isolation,
            execution=constitution.execution,
        )
        record["observed_state"] = _write_patch_snapshot(
            iteration_dir / "observed.patch", worktree, base_commit
        )
        record["scope"] = {
            "passed": scope.passed,
            "summary": scope.summary,
            "details": scope.details,
        }
        record["quick_gates"] = quick

        control_failures = [
            f"gate altered the frozen sensor {name}"
            for name in _altered_sensors(sensor_paths, sensor_evidence)
        ]
        control_failures += _changed_environment(
            evidence["environment"], worktree, constitution.execution
        )
        if control_failures:
            evidence["iterations"].append(record)
            persist()
            return finish("REJECTED", control_failures)

        iteration_passed = scope.passed and all(g["passed"] for g in quick)
        if iteration_passed:
            record["controller_feedback"] = None
            evidence["iterations"].append(record)
            persist()
            accepted_quick = quick
            break

        feedback_parts = []
        if not scope.passed:
            feedback_parts.append("Structural invariant failures:\n" + scope.summary)
        feedback_parts.append(_gate_feedback(quick))
        feedback = "\n\n".join(x for x in feedback_parts if x).strip()
        feedback_path = iteration_dir / "controller-feedback.md"
        feedback_path.write_text(feedback, encoding="utf-8")
        record["controller_feedback"] = {
            "path": str(feedback_path),
            "sha256": sha256_text(feedback),
            "text": feedback,
        }
        evidence["iterations"].append(record)
        persist()

    if accepted_quick is None:
        return finish(
            "REJECTED",
            [f"quick gates did not converge within {max_iterations} iterations"],
        )

    full = run_gates(
        repo=worktree,
        gates=constitution.gates_for("full"),
        out_dir=run_dir / "full",
        sensor_paths=sensor_paths,
        isolation=gate_isolation,
        execution=constitution.execution,
    )
    evidence["full_gates"] = full
    persist()
    reasons = [
        f"gate altered the frozen sensor {name}"
        for name in _altered_sensors(sensor_paths, sensor_evidence)
    ]
    reasons += _changed_environment(
        evidence["environment"], worktree, constitution.execution
    )
    if not all(g["passed"] for g in full):
        reasons.append("full gate failed")
    if reasons:
        return finish("REJECTED", reasons)

    # Deterministic runtime evidence the read-only reviewer cannot produce
    # itself, built only once every gate passed and every sensor is intact.
    observations = _observations(
        constitution, accepted_quick, full, (run_dir, worktree)
    )
    # Serialized once: the prompted bytes are the hashed bytes.
    observations_json = json.dumps(
        observations, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )

    schema_path = _review_schema_path()
    review_prompt_text = reviewer_prompt(task, constitution, observations_json)
    review_prompt_path = run_dir / "review" / "prompt.md"
    review_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    review_prompt_path.write_text(review_prompt_text, encoding="utf-8")
    # The reviewer is a read-only sensor: it reads the candidate and writes
    # nothing into it. The adapter denies those writes itself; this describes
    # the confinement it runs under, and is recorded before it starts.
    review_isolation = Isolation(
        denied=isolation.denied, read_only=(*isolation.read_only, worktree)
    )
    # Recorded before the reviewer runs, so a reviewer failure cannot erase the
    # observations it was given.
    evidence["review"] = {
        "prompt": {
            "path": str(review_prompt_path),
            "sha256": sha256_text(review_prompt_text),
        },
        "observations": observations,
        "observations_sha256": sha256_text(observations_json),
        "isolation": review_backend.describe_isolation(review_isolation),
    }
    persist()
    try:
        review, review_meta = review_backend.review(
            worktree=worktree,
            prompt=review_prompt_text,
            schema_path=schema_path,
            out_dir=run_dir / "review",
            model=review_model,
            timeout_seconds=agent_timeout_seconds,
            isolation=isolation,
            effort=review_effort,
            speed=review_speed,
        )
    except (ActuatorError, SandboxError) as exc:
        return finish("REJECTED", [str(exc)])

    _record_actuation(inference["reviewer"], review_meta)
    evidence["review"].update(
        {
            "result": review,
            "result_sha256": sha256_json(review),
            "meta": review_meta,
        }
    )
    reasons = _review_decision(
        review, task.criteria, constitution.review.blocking_severities
    )
    return finish("ACCEPTED" if not reasons else "REJECTED", reasons)
