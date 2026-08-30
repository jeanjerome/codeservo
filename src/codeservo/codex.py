from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path


class CodexError(RuntimeError):
    pass


def _base_command(worktree: Path, sandbox: str, model: str | None) -> list[str]:
    command = [
        "codex",
        "exec",
        "--cd",
        str(worktree),
        "--sandbox",
        sandbox,
        "--ephemeral",
    ]
    if model:
        command.extend(["--model", model])
    return command


def run_implementer(
    *,
    worktree: Path,
    prompt: str,
    out_dir: Path,
    model: str | None,
    timeout_seconds: int,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    events = out_dir / "events.jsonl"
    stderr_path = out_dir / "stderr.log"
    last_message = out_dir / "last-message.md"
    command = _base_command(worktree, "workspace-write", model)
    command.extend(["--json", "--output-last-message", str(last_message), "-"])

    started = time.monotonic()
    with events.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            completed = subprocess.run(
                command,
                input=prompt.encode("utf-8"),
                stdout=stdout,
                stderr=stderr,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodexError(f"implementer timed out after {timeout_seconds}s") from exc

    return {
        "exit_code": completed.returncode,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "events_path": str(events),
        "stderr_path": str(stderr_path),
        "last_message_path": str(last_message),
    }


def run_reviewer(
    *,
    worktree: Path,
    prompt: str,
    schema_path: Path,
    out_dir: Path,
    model: str | None,
    timeout_seconds: int,
) -> tuple[dict, dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = out_dir / "stdout.log"
    stderr_path = out_dir / "stderr.log"
    result_path = out_dir / "review.json"
    command = _base_command(worktree, "read-only", model)
    command.extend(
        [
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(result_path),
            "-",
        ]
    )

    started = time.monotonic()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            completed = subprocess.run(
                command,
                input=prompt.encode("utf-8"),
                stdout=stdout,
                stderr=stderr,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodexError(f"reviewer timed out after {timeout_seconds}s") from exc

    meta = {
        "exit_code": completed.returncode,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "result_path": str(result_path),
    }
    if completed.returncode != 0:
        raise CodexError(f"reviewer exited with {completed.returncode}; see {stderr_path}")
    try:
        return json.loads(result_path.read_text(encoding="utf-8")), meta
    except (OSError, json.JSONDecodeError) as exc:
        raise CodexError(f"invalid reviewer output: {result_path}") from exc
