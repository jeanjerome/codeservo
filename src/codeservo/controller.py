from __future__ import annotations

import json
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .actuator import Actuator, ActuatorError, default_actuator_name, load_actuator
from .config import load_constitution
from .evidence import (
    relative_evidence_paths,
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
from .model import Constitution
from .process import tail
from .prompts import implementer_prompt, reviewer_prompt
from .sandbox import Isolation, SandboxError, isolation_evidence
from .task import load_task


class ControlFailure(RuntimeError):
    pass


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
    output = completed.stdout.strip() or completed.stderr.strip()
    return output.splitlines()[0] if output else "unavailable"


def _runtime_metadata(
    actuator: Actuator, model: str | None, review_model: str | None
) -> dict:
    source_root = Path(__file__).resolve().parents[2]
    return {
        "codeservo_version": __version__,
        "codeservo_commit": _command_version(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"]
        ),
        "actuator": actuator.name,
        "actuator_version": _command_version(list(actuator.version_command)),
        "implementer_model": model or f"{actuator.name}-default",
        "reviewer_model": review_model or f"{actuator.name}-default",
        "python_version": platform.python_version(),
        "git_version": _command_version(["git", "--version"]),
    }


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


def _altered_sensors(
    sensor_paths: dict[str, Path], sensor_evidence: dict[str, dict]
) -> list[str]:
    """Frozen sensors whose content changed after the controller froze them."""
    return sorted(
        name
        for name, path in sensor_paths.items()
        if sha256_path(path) != sensor_evidence[name]["sha256"]
    )


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
) -> dict:
    backend = load_actuator(actuator or default_actuator_name())
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
        "schema_version": 6,
        "run_id": run_id,
        "started_at": _now(),
        "repo": str(repo),
        "state_dir": str(state_root),
        "base_commit": base_commit,
        "task_sha256": sha256_text(task.raw_text),
        "constitution_sha256": sha256_text(constitution.raw_text),
        "runtime": _runtime_metadata(backend, model, review_model),
        "sensors": sensor_evidence,
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

    if not is_clean(repo):
        return finish("REJECTED", ["source repository is not clean"])

    baseline = run_gates(
        repo=repo,
        gates=baseline_gates(constitution),
        out_dir=run_dir / "baseline",
        isolation=gate_isolation,
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
    feedback = ""
    quick_passed = False

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
            )
        except (ActuatorError, SandboxError) as exc:
            record["agent_error"] = str(exc)
            evidence["iterations"].append(record)
            persist()
            return finish("REJECTED", [str(exc)])

        record["agent"] = agent
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

        altered = _altered_sensors(sensor_paths, sensor_evidence)
        if altered:
            evidence["iterations"].append(record)
            persist()
            return finish(
                "REJECTED",
                [f"gate altered the frozen sensor {name}" for name in altered],
            )

        iteration_passed = scope.passed and all(g["passed"] for g in quick)
        if iteration_passed:
            record["controller_feedback"] = None
            evidence["iterations"].append(record)
            persist()
            quick_passed = True
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

    if not quick_passed:
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
    )
    evidence["full_gates"] = full
    persist()
    reasons = [
        f"gate altered the frozen sensor {name}"
        for name in _altered_sensors(sensor_paths, sensor_evidence)
    ]
    if not all(g["passed"] for g in full):
        reasons.append("full gate failed")
    if reasons:
        return finish("REJECTED", reasons)

    schema_path = Path(__file__).resolve().parents[2] / "templates" / "review.schema.json"
    # Installed wheels do not contain repository-level templates; fall back to package copy.
    if not schema_path.exists():
        schema_path = Path(__file__).with_name("review.schema.json")

    review_prompt_text = reviewer_prompt(task, constitution)
    review_prompt_path = run_dir / "review" / "prompt.md"
    review_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    review_prompt_path.write_text(review_prompt_text, encoding="utf-8")
    try:
        review, review_meta = backend.review(
            worktree=worktree,
            prompt=review_prompt_text,
            schema_path=schema_path,
            out_dir=run_dir / "review",
            model=review_model,
            timeout_seconds=agent_timeout_seconds,
            isolation=isolation,
        )
    except (ActuatorError, SandboxError) as exc:
        return finish("REJECTED", [str(exc)])

    evidence["review"] = {
        "prompt": {
            "path": str(review_prompt_path),
            "sha256": sha256_text(review_prompt_text),
        },
        "result": review,
        "result_sha256": sha256_json(review),
        "meta": review_meta,
    }
    reasons = _review_decision(
        review, task.criteria, constitution.review.blocking_severities
    )
    return finish("ACCEPTED" if not reasons else "REJECTED", reasons)
