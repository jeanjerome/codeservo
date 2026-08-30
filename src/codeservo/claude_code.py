from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .actuator import ActuatorError, Isolation, isolation_evidence, seatbelt_command
from .evidence import sha256_file, sha256_record, sha256_text

# `--safe-mode` drops user memory, skills, plugins, hooks, custom agents and
# settings files, so the actuator only sees the frozen prompt and the worktree.
HERMETIC_FLAGS = (
    "--print",
    "--safe-mode",
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--no-session-persistence",
)
IMPLEMENTER_TOOLS = "Bash,Read,Write,Edit,NotebookEdit"
REVIEWER_TOOLS = "Bash,Read"


class ClaudeCodeError(ActuatorError):
    pass


def _base_command(*, model: str | None, tools: str) -> list[str]:
    command = [
        "claude",
        *HERMETIC_FLAGS,
        # Confinement comes from the controller-owned seatbelt profile, so the
        # agent never nests a second sandbox of its own.
        "--permission-mode",
        "bypassPermissions",
        "--tools",
        tools,
    ]
    if model:
        command.extend(["--model", model])
    return command


def _inline_schema(schema_path: Path) -> str:
    """Render the frozen review schema as Claude Code accepts it.

    The CLI validates the schema with a draft-07 validator that rejects the
    2020-12 `$schema` reference, so the dialect declaration is dropped.
    """
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClaudeCodeError(f"invalid review schema: {schema_path}") from exc
    schema.pop("$schema", None)
    return json.dumps(schema, separators=(",", ":"), sort_keys=True)


def _events(path: Path) -> list[dict]:
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _result_event(events: list[dict]) -> dict | None:
    for event in reversed(events):
        if event.get("type") == "result":
            return event
    return None


def _session(result: dict | None) -> dict:
    if result is None:
        return {}
    return {
        "session_id": result.get("session_id"),
        "subtype": result.get("subtype"),
        "is_error": bool(result.get("is_error")),
        "num_turns": result.get("num_turns"),
        "total_cost_usd": result.get("total_cost_usd"),
        "terminal_reason": result.get("terminal_reason"),
    }


def describe_isolation(isolation: Isolation) -> dict:
    return isolation_evidence(isolation, "macos-sandbox-exec")


def run_implementer(
    *,
    worktree: Path,
    prompt: str,
    out_dir: Path,
    model: str | None,
    timeout_seconds: int,
    isolation: Isolation = Isolation(),
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / "events.jsonl"
    stderr_path = out_dir / "stderr.log"
    last_message = out_dir / "last-message.md"
    command = _base_command(model=model, tools=IMPLEMENTER_TOOLS)
    command.extend(["--output-format", "stream-json", "--verbose"])
    started = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="codeservo-agent-") as temp:
        temporary_dir = Path(temp)
        temporary_events = temporary_dir / "events.jsonl"
        temporary_stderr = temporary_dir / "stderr.log"

        timeout_error: subprocess.TimeoutExpired | None = None
        with temporary_events.open("wb") as stdout, temporary_stderr.open(
            "wb"
        ) as stderr:
            try:
                completed = subprocess.run(
                    seatbelt_command(command, isolation),
                    input=prompt.encode("utf-8"),
                    cwd=worktree,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                timeout_error = exc
        shutil.copyfile(temporary_events, events_path)
        shutil.copyfile(temporary_stderr, stderr_path)
        if timeout_error is not None:
            raise ClaudeCodeError(
                f"implementer timed out after {timeout_seconds}s"
            ) from timeout_error

    result = _result_event(_events(events_path))
    message = str(result.get("result", "")) if result else ""
    last_message.write_text(message, encoding="utf-8")

    record = {
        "command": command,
        "exit_code": completed.returncode,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "session": _session(result),
        "events_path": str(events_path),
        "events_sha256": sha256_file(events_path),
        "stderr_path": str(stderr_path),
        "stderr_sha256": sha256_file(stderr_path),
        "last_message_path": str(last_message),
        "last_message_sha256": sha256_text(message),
    }
    record["result_sha256"] = sha256_record(record)
    if completed.returncode == 0 and record["session"].get("is_error"):
        raise ClaudeCodeError(
            f"implementer reported {record['session'].get('subtype')}; see {stderr_path}"
        )
    return record


def run_reviewer(
    *,
    worktree: Path,
    prompt: str,
    schema_path: Path,
    out_dir: Path,
    model: str | None,
    timeout_seconds: int,
    isolation: Isolation = Isolation(),
) -> tuple[dict, dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = out_dir / "stdout.log"
    stderr_path = out_dir / "stderr.log"
    result_path = out_dir / "review.json"
    command = _base_command(model=model, tools=REVIEWER_TOOLS)
    command.extend(
        ["--output-format", "json", "--json-schema", _inline_schema(schema_path)]
    )
    # The reviewer is a read-only sensor: the seatbelt denies every write to the
    # candidate worktree instead of trusting the prompt.
    review_isolation = Isolation(
        denied=isolation.denied,
        read_only=(*isolation.read_only, worktree),
    )

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="codeservo-review-") as temp:
        temporary_dir = Path(temp)
        temporary_stdout = temporary_dir / "stdout.log"
        temporary_stderr = temporary_dir / "stderr.log"
        with temporary_stdout.open("wb") as stdout, temporary_stderr.open(
            "wb"
        ) as stderr:
            try:
                completed = subprocess.run(
                    seatbelt_command(command, review_isolation),
                    input=prompt.encode("utf-8"),
                    cwd=worktree,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ClaudeCodeError(
                    f"reviewer timed out after {timeout_seconds}s"
                ) from exc
        shutil.copyfile(temporary_stdout, stdout_path)
        shutil.copyfile(temporary_stderr, stderr_path)

    review = _review_result(stdout_path, stderr_path)
    result_path.write_text(
        json.dumps(review, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    meta = {
        "command": command,
        "exit_code": completed.returncode,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "schema_sha256": sha256_file(schema_path),
        "stdout_path": str(stdout_path),
        "stdout_sha256": sha256_file(stdout_path),
        "stderr_path": str(stderr_path),
        "stderr_sha256": sha256_file(stderr_path),
        "result_path": str(result_path),
        "result_sha256": sha256_file(result_path),
    }
    if completed.returncode != 0:
        meta["meta_sha256"] = sha256_record(meta)
        raise ClaudeCodeError(
            f"reviewer exited with {completed.returncode}; see {stderr_path}"
        )
    meta["session"] = _session(_result_event(_events(stdout_path)))
    meta["meta_sha256"] = sha256_record(meta)
    return review, meta


def _review_result(stdout_path: Path, stderr_path: Path) -> dict:
    try:
        payload = json.loads(stdout_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClaudeCodeError(f"invalid reviewer output: {stdout_path}") from exc
    if not isinstance(payload, dict) or payload.get("is_error"):
        raise ClaudeCodeError(f"reviewer reported an error; see {stderr_path}")

    structured = payload.get("structured_output")
    if isinstance(structured, dict):
        return structured
    try:
        review = json.loads(str(payload.get("result", "")))
    except json.JSONDecodeError as exc:
        raise ClaudeCodeError(
            f"reviewer did not return schema-shaped output: {stdout_path}"
        ) from exc
    if not isinstance(review, dict):
        raise ClaudeCodeError(
            f"reviewer did not return schema-shaped output: {stdout_path}"
        )
    return review
