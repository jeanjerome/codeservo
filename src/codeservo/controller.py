from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .codex import CodexError, run_implementer, run_reviewer
from .config import load_constitution
from .evidence import sha256_text, write_json
from .gates import baseline_gates, run_gates
from .git import create_worktree, head, is_clean, make_patch, root, scope_sensor
from .process import tail
from .prompts import implementer_prompt, reviewer_prompt
from .task import load_task


class ControlFailure(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


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
) -> dict:
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

    evidence: dict = {
        "schema_version": 2,
        "run_id": run_id,
        "started_at": _now(),
        "repo": str(repo),
        "state_dir": str(state_root),
        "base_commit": base_commit,
        "task_sha256": sha256_text(task.raw_text),
        "constitution_sha256": sha256_text(constitution.raw_text),
        "status": "RUNNING",
        "iterations": [],
        "decision": {"reasons": []},
        "run_dir": str(run_dir),
        "worktree": None,
    }
    evidence_path = run_dir / "evidence.json"
    write_json(evidence_path, evidence)

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
        write_json(evidence_path, evidence)
        return evidence

    if not is_clean(repo):
        return finish("REJECTED", ["source repository is not clean"])

    baseline = run_gates(repo=repo, gates=baseline_gates(constitution), out_dir=run_dir / "baseline")
    evidence["baseline"] = baseline
    write_json(evidence_path, evidence)
    if not all(g["passed"] for g in baseline):
        return finish("REJECTED", ["baseline gate failed"])
    if not is_clean(repo):
        return finish("REJECTED", ["baseline gate mutated the source repository"])

    create_worktree(repo, worktree, base_commit)
    evidence["worktree"] = str(worktree)
    write_json(evidence_path, evidence)
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
            agent = run_implementer(
                worktree=worktree,
                prompt=prompt,
                out_dir=iteration_dir / "agent",
                model=model,
                timeout_seconds=agent_timeout_seconds,
            )
        except CodexError as exc:
            record["agent_error"] = str(exc)
            evidence["iterations"].append(record)
            write_json(evidence_path, evidence)
            return finish("REJECTED", [str(exc)])

        record["agent"] = agent
        record["actuator_state"] = _write_patch_snapshot(
            iteration_dir / "actuator.patch", worktree, base_commit
        )
        if agent["exit_code"] != 0:
            evidence["iterations"].append(record)
            write_json(evidence_path, evidence)
            return finish("REJECTED", [f"implementer exited with {agent['exit_code']}"])

        scope = scope_sensor(worktree, base_commit, constitution.scope)
        quick = run_gates(
            repo=worktree,
            gates=constitution.gates_for("quick"),
            out_dir=iteration_dir / "quick",
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

        iteration_passed = scope.passed and all(g["passed"] for g in quick)
        if iteration_passed:
            record["controller_feedback"] = None
            evidence["iterations"].append(record)
            write_json(evidence_path, evidence)
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
        write_json(evidence_path, evidence)

    if not quick_passed:
        return finish("REJECTED", [f"quick gates did not converge within {max_iterations} iterations"])

    full = run_gates(
        repo=worktree,
        gates=constitution.gates_for("full"),
        out_dir=run_dir / "full",
    )
    evidence["full_gates"] = full
    write_json(evidence_path, evidence)
    if not all(g["passed"] for g in full):
        return finish("REJECTED", ["full gate failed"])

    schema_path = Path(__file__).resolve().parents[2] / "templates" / "review.schema.json"
    # Installed wheels do not contain repository-level templates; fall back to package copy.
    if not schema_path.exists():
        schema_path = Path(__file__).with_name("review.schema.json")

    review_prompt_text = reviewer_prompt(task, constitution)
    (run_dir / "review" / "prompt.md").parent.mkdir(parents=True, exist_ok=True)
    (run_dir / "review" / "prompt.md").write_text(review_prompt_text, encoding="utf-8")
    try:
        review, review_meta = run_reviewer(
            worktree=worktree,
            prompt=review_prompt_text,
            schema_path=schema_path,
            out_dir=run_dir / "review",
            model=review_model,
            timeout_seconds=agent_timeout_seconds,
        )
    except CodexError as exc:
        return finish("REJECTED", [str(exc)])

    evidence["review"] = {"result": review, "meta": review_meta}
    reasons = _review_decision(review, task.criteria, constitution.review.blocking_severities)
    return finish("ACCEPTED" if not reasons else "REJECTED", reasons)
