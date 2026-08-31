from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .actuator import ActuatorError
from .evidence import sha256_file, sha256_record, sha256_text
from .models import DEFAULT_SPEED
from .sandbox import Isolation, isolation_evidence, seatbelt_command

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

# `--safe-mode` drops every settings source the user owns, so the only document
# `--settings` can point at is this one, which CodeServo writes itself.
FAST_MODE_SETTINGS = {"fastMode": True}


class ClaudeCodeError(ActuatorError):
    pass


def _base_command(
    *, model: str | None, tools: str, effort: str | None = None
) -> list[str]:
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
    if effort:
        command.extend(["--effort", effort])
    return command


def _implementer_command(
    *,
    model: str | None,
    effort: str | None,
    speed: str,
    settings_dir: Path,
) -> tuple[list[str], dict]:
    """The implementer command and the profile values it carries.

    The fast tier is a settings document rather than a flag, and that document
    lives only as long as the run, so the returned record keeps its content
    instead of a path that stops existing.
    """
    command = _base_command(model=model, tools=IMPLEMENTER_TOOLS, effort=effort)
    command.extend(["--output-format", "stream-json", "--verbose"])
    native: dict = {}
    if effort:
        native["--effort"] = effort
    if speed == "fast":
        settings_path = settings_dir / "settings.json"
        settings_path.write_text(json.dumps(FAST_MODE_SETTINGS), encoding="utf-8")
        command.extend(["--settings", str(settings_path)])
        native["--settings"] = dict(FAST_MODE_SETTINGS)
    return command, native


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
    """Read a stream of events, or the single object a `json` run produces."""
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        pass
    else:
        return [payload] if isinstance(payload, dict) else []

    events: list[dict] = []
    for line in text.splitlines():
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


def _models(events: list[dict]) -> dict:
    """Report the models a session ran on.

    The command line carries an alias such as `opus`, which moves over time. The
    session reports the identifier it resolved to, and its usage record names
    every model that spent tokens, so a run stays comparable to another one.
    """
    session_model = next(
        (
            event.get("model")
            for event in events
            if event.get("type") == "system" and event.get("subtype") == "init"
        ),
        None,
    )
    usage = (_result_event(events) or {}).get("modelUsage")
    spent = {}
    if isinstance(usage, dict):
        for name, record in usage.items():
            if isinstance(record, dict):
                spent[name] = {
                    "output_tokens": record.get("outputTokens"),
                    "cost_usd": record.get("costUSD"),
                }
    return {"session_model": session_model, "usage": spent}


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


def _observed(events: list[dict]) -> dict:
    """The inference profile the session reported about itself.

    The init event names the model the alias resolved to. Claude Code reports
    neither a reasoning effort nor a speed tier in its stream, so both stay
    unknown rather than repeating what was asked for.
    """
    return {
        "model": _models(events)["session_model"],
        "effort": None,
        "speed": None,
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
    effort: str | None = None,
    speed: str = DEFAULT_SPEED,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / "events.jsonl"
    stderr_path = out_dir / "stderr.log"
    last_message = out_dir / "last-message.md"
    started = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="codeservo-agent-") as temp:
        temporary_dir = Path(temp)
        temporary_events = temporary_dir / "events.jsonl"
        temporary_stderr = temporary_dir / "stderr.log"
        command, native = _implementer_command(
            model=model, effort=effort, speed=speed, settings_dir=temporary_dir
        )

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

    events = _events(events_path)
    result = _result_event(events)
    message = str(result.get("result", "")) if result else ""
    last_message.write_text(message, encoding="utf-8")

    record = {
        "command": command,
        "exit_code": completed.returncode,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "session": _session(result),
        "models": _models(events),
        "native": native,
        "observed": _observed(events),
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

    # A failed process explains itself through its exit code; parsing its output
    # first would report a missing review instead of the reason it is missing.
    if completed.returncode != 0:
        raise ClaudeCodeError(
            f"reviewer exited with {completed.returncode}; see {stderr_path}"
        )
    review = _review_result(stdout_path, stderr_path)
    result_path.write_text(
        json.dumps(review, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    meta = {
        "command": command,
        "exit_code": completed.returncode,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "schema_sha256": sha256_file(schema_path),
        "session": _session(_result_event(_events(stdout_path))),
        "models": _models(_events(stdout_path)),
        "stdout_path": str(stdout_path),
        "stdout_sha256": sha256_file(stdout_path),
        "stderr_path": str(stderr_path),
        "stderr_sha256": sha256_file(stderr_path),
        "result_path": str(result_path),
        "result_sha256": sha256_file(result_path),
    }
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
