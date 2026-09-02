from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from ..evidence.digests import sha256_file, sha256_record
from ..runtime.sandbox import Isolation, isolation_evidence, seatbelt_command
from .base import ActuatorError
from .inventory import DEFAULT_SPEED

# The two configuration keys Codex accepts for the inference profile, and the
# event-stream fields it answers with. The names differ on purpose: a key sets
# the request, a field reports what the session ran on.
EFFORT_KEY = "model_reasoning_effort"
SPEED_KEY = "service_tier"
FAST_SPEED_VALUE = "priority"
OBSERVED_FIELDS = {
    "model": "model",
    "effort": "reasoning_effort",
    "speed": "service_tier",
}


class CodexError(ActuatorError):
    pass


def _base_command(
    worktree: Path,
    sandbox: str,
    model: str | None,
    effort: str | None = None,
    speed: str = DEFAULT_SPEED,
) -> list[str]:
    command = [
        "codex",
        "exec",
        "--cd",
        str(worktree),
        "--sandbox",
        sandbox,
        "--ephemeral",
        "--ignore-user-config",
    ]
    if model:
        command.extend(["--model", model])
    if effort:
        command.extend(["-c", f"{EFFORT_KEY}={effort}"])
    if speed == "fast":
        command.extend(["-c", f"{SPEED_KEY}={FAST_SPEED_VALUE}"])
    return command


def _native_profile(effort: str | None, speed: str) -> dict:
    """The configuration keys the command actually carried, and their values."""
    native: dict = {}
    if effort:
        native[EFFORT_KEY] = effort
    if speed == "fast":
        native[SPEED_KEY] = FAST_SPEED_VALUE
    return native


def _events(path: Path) -> list[dict]:
    events: list[dict] = []
    text = path.read_text(encoding="utf-8", errors="replace")
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


def _observed(events: list[dict]) -> dict:
    """The inference profile the session reported about itself.

    Codex answers under its own field names, which are not the configuration
    keys that asked for them, so the stream is read by name. The installed
    version names none of the three in either role, and every field then stays
    empty rather than borrowing the request; a version that starts naming one
    fills that field with no further change here. The last report wins, because
    it describes the session as it ended.
    """
    observed: dict = {name: None for name in OBSERVED_FIELDS}
    for event in events:
        for scope in (event, event.get("msg")):
            if not isinstance(scope, dict):
                continue
            for name, field in OBSERVED_FIELDS.items():
                reported = scope.get(field)
                if isinstance(reported, str) and reported:
                    observed[name] = reported
    return observed


def _sandbox(isolation: Isolation, native: str) -> str:
    """Select the sandbox Codex applies to itself.

    macOS refuses to apply a seatbelt profile inside another one, so Codex keeps
    its own sandbox only while the controller does not confine it. Otherwise the
    controller-owned profile is the single confinement authority.
    """
    return native if isolation.empty else "danger-full-access"


def describe_isolation(isolation: Isolation) -> dict:
    return isolation_evidence(
        isolation,
        "codex-workspace-write" if isolation.empty else "macos-sandbox-exec",
    )


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
    events = out_dir / "events.jsonl"
    stderr_path = out_dir / "stderr.log"
    last_message = out_dir / "last-message.md"
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="codeservo-agent-") as temp:
        temporary_dir = Path(temp)
        temporary_events = temporary_dir / "events.jsonl"
        temporary_stderr = temporary_dir / "stderr.log"
        temporary_last_message = temporary_dir / "last-message.md"
        command = _base_command(
            worktree, _sandbox(isolation, "workspace-write"), model, effort, speed
        )
        command.extend(
            ["--json", "--output-last-message", str(temporary_last_message), "-"]
        )
        command = seatbelt_command(command, isolation)

        timeout_error: subprocess.TimeoutExpired | None = None
        with temporary_events.open("wb") as stdout, temporary_stderr.open(
            "wb"
        ) as stderr:
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
                timeout_error = exc
        shutil.copyfile(temporary_events, events)
        shutil.copyfile(temporary_stderr, stderr_path)
        if temporary_last_message.is_file():
            shutil.copyfile(temporary_last_message, last_message)
        if timeout_error is not None:
            raise CodexError(
                f"implementer timed out after {timeout_seconds}s"
            ) from timeout_error

    result = {
        "exit_code": completed.returncode,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "native": _native_profile(effort, speed),
        "observed": _observed(_events(events)),
        "events_path": str(events),
        "events_sha256": sha256_file(events),
        "stderr_path": str(stderr_path),
        "stderr_sha256": sha256_file(stderr_path),
        "last_message_path": str(last_message),
        "last_message_sha256": (
            sha256_file(last_message) if last_message.is_file() else None
        ),
    }
    result["result_sha256"] = sha256_record(result)
    return result


def run_reviewer(
    *,
    worktree: Path,
    prompt: str,
    schema_path: Path,
    out_dir: Path,
    model: str | None,
    timeout_seconds: int,
    isolation: Isolation = Isolation(),
    effort: str | None = None,
    speed: str = DEFAULT_SPEED,
) -> tuple[dict, dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = out_dir / "stdout.log"
    stderr_path = out_dir / "stderr.log"
    result_path = out_dir / "review.json"
    # A confined reviewer loses its native read-only sandbox, so the
    # controller-owned profile denies every write to the candidate worktree.
    review_isolation = (
        isolation
        if isolation.empty
        else Isolation(
            denied=isolation.denied,
            read_only=(*isolation.read_only, worktree),
        )
    )
    started = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="codeservo-review-") as temp:
        temporary_result = Path(temp) / "review.json"
        command = _base_command(
            worktree, _sandbox(isolation, "read-only"), model, effort, speed
        )
        # `--json` turns stdout into the documented event stream the
        # implementer already receives, so the reviewer reports about its own
        # session under the same field names. Its answer is unaffected: it
        # keeps coming from the file `--output-last-message` names.
        command.extend(
            [
                "--json",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(temporary_result),
                "-",
            ]
        )
        command = seatbelt_command(command, review_isolation)

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
                raise CodexError(
                    f"reviewer timed out after {timeout_seconds}s"
                ) from exc
        if temporary_result.is_file():
            shutil.copyfile(temporary_result, result_path)

    meta = {
        "exit_code": completed.returncode,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "native": _native_profile(effort, speed),
        # Read from the event stream `--json` produces, under the names the
        # stream uses, and never from the keys the command line carried.
        "observed": _observed(_events(stdout_path)),
        "stdout_path": str(stdout_path),
        "stdout_sha256": sha256_file(stdout_path),
        "stderr_path": str(stderr_path),
        "stderr_sha256": sha256_file(stderr_path),
        "result_path": str(result_path),
        "result_sha256": (
            sha256_file(result_path) if result_path.is_file() else None
        ),
    }
    meta["meta_sha256"] = sha256_record(meta)
    if completed.returncode != 0:
        raise CodexError(f"reviewer exited with {completed.returncode}; see {stderr_path}")
    try:
        return json.loads(result_path.read_text(encoding="utf-8")), meta
    except (OSError, json.JSONDecodeError) as exc:
        raise CodexError(f"invalid reviewer output: {result_path}") from exc
