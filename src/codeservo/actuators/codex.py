from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from ..domain.document import Document
from ..evidence.digests import sha256_file, sha256_record
from ..runtime.sandbox import (
    Isolation,
    IsolationEvidence,
    isolation_evidence,
    seatbelt_command,
)
from .base import ActuatorError, Billed, ObservedProfile, Tokens, Usage

# The configuration key Codex accepts for the reasoning effort, and the
# event-stream fields it answers with. The names differ on purpose: a key sets
# the request, a field reports what the session ran on.
EFFORT_KEY = "model_reasoning_effort"
MODEL_FLAG = "--model"
OBSERVED_FIELDS = {
    "model": "model",
    "effort": "reasoning_effort",
}
# Where the stream reports what a turn consumed, and the names it uses. The
# input count is a total: the cached and the written parts sit inside it, as
# the provider's caching guide states, so the uncached input is what remains.
USAGE_EVENT = "turn.completed"
USAGE_FIELD = "usage"


@dataclass(frozen=True, kw_only=True)
class UnsignedActuation(Document):
    """One actuation, before it closes over itself."""

    exit_code: int
    duration_ms: int
    native: dict[str, Any]
    observed: ObservedProfile
    usage: Usage
    events_path: str
    events_sha256: str
    stderr_path: str
    stderr_sha256: str
    last_message_path: str
    last_message_sha256: str | None

    def signed(self) -> CodexActuation:
        """This actuation, closed over the digest of what it holds."""
        carried = {
            declared.name: getattr(self, declared.name)
            for declared in fields(UnsignedActuation)
        }
        return CodexActuation(
            **carried, result_sha256=sha256_record(self.to_document())
        )


@dataclass(frozen=True, kw_only=True)
class CodexActuation(UnsignedActuation):
    """One actuation, and the digest recomputable from what it holds."""

    result_sha256: str


@dataclass(frozen=True, kw_only=True)
class UnsignedReviewMeta(Document):
    """One review call, before it closes over itself."""

    exit_code: int
    duration_ms: int
    native: dict[str, Any]
    observed: ObservedProfile
    usage: Usage
    stdout_path: str
    stdout_sha256: str
    stderr_path: str
    stderr_sha256: str
    result_path: str
    result_sha256: str | None

    def signed(self) -> CodexReviewMeta:
        """This review call, closed over the digest of what it holds."""
        carried = {
            declared.name: getattr(self, declared.name)
            for declared in fields(UnsignedReviewMeta)
        }
        return CodexReviewMeta(
            **carried, meta_sha256=sha256_record(self.to_document())
        )


@dataclass(frozen=True, kw_only=True)
class CodexReviewMeta(UnsignedReviewMeta):
    """One review call, and the digest recomputable from what it holds."""

    meta_sha256: str


class CodexError(ActuatorError):
    pass


def _base_command(worktree: Path, sandbox: str, model: str, effort: str) -> list[str]:
    """The command both roles start from: the model as a flag, the effort as a key.

    Both are handed over unchanged. Whether the model accepts the effort is
    the CLI's to decide, and it fails explicitly when it does not.
    """
    return [
        "codex",
        "exec",
        "--cd",
        str(worktree),
        "--sandbox",
        sandbox,
        "--ephemeral",
        "--ignore-user-config",
        MODEL_FLAG,
        model,
        "-c",
        f"{EFFORT_KEY}={effort}",
    ]


def _native_profile(model: str, effort: str) -> dict[str, Any]:
    """The flag and the configuration key the command carried, and their values."""
    return {MODEL_FLAG: model, EFFORT_KEY: effort}


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


def _observed(events: list[dict]) -> ObservedProfile:
    """The inference profile the session reported about itself.

    Codex answers under its own field names, which are not the configuration
    keys that asked for them, so the stream is read by name. The installed
    version names none of the three in either role, and every field then stays
    empty rather than borrowing the request; a version that starts naming one
    fills that field with no further change here. The last report wins, because
    it describes the session as it ended.
    """
    read: dict[str, str | None] = dict.fromkeys(OBSERVED_FIELDS)
    for event in events:
        for scope in (event, event.get("msg")):
            if not isinstance(scope, dict):
                continue
            for name, field in OBSERVED_FIELDS.items():
                reported = scope.get(field)
                if isinstance(reported, str) and reported:
                    read[name] = reported
    return ObservedProfile(model=read["model"], effort=read["effort"])


def _count(record: dict, field: str) -> int | None:
    value = record.get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _usage(events: list[dict]) -> Usage:
    """What the session consumed, summed over the turns that reported it.

    codex-cli 0.151.0 reports, per completed turn, a total input count with
    the cached and the written parts inside it, an output count and the
    reasoning part of it. It names no model, no cost and no cache duration, so
    the tokens are billed under no model here: the controller rates them at
    the model it requested, and says so. A session that completed no turn
    reported nothing.
    """
    usages = [
        event[USAGE_FIELD]
        for event in events
        if event.get("type") == USAGE_EVENT and isinstance(event.get(USAGE_FIELD), dict)
    ]
    if not usages:
        return Usage(billed=(), cache_write_duration=None)
    totals: dict[str, int | None] = {
        "input": 0,
        "cached_input": 0,
        "cache_write": 0,
        "output": 0,
        "reasoning": 0,
    }
    reported = {
        "input": "input_tokens",
        "cached_input": "cached_input_tokens",
        "cache_write": "cache_write_input_tokens",
        "output": "output_tokens",
        "reasoning": "reasoning_output_tokens",
    }
    for usage in usages:
        for category, field in reported.items():
            count = _count(usage, field)
            current = totals[category]
            totals[category] = None if count is None or current is None else current + count
    total_input, cached, written = totals["input"], totals["cached_input"], totals["cache_write"]
    uncached = (
        max(total_input - cached - written, 0)
        if total_input is not None and cached is not None and written is not None
        else None
    )
    return Usage(
        billed=(
            Billed(
                model=None,
                tokens=Tokens(
                    input=uncached,
                    cached_input=cached,
                    cache_write=written,
                    output=totals["output"],
                    reasoning=totals["reasoning"],
                ),
                reported_cost_usd=None,
            ),
        ),
        cache_write_duration=None,
    )


def _sandbox(isolation: Isolation, native: str) -> str:
    """Select the sandbox Codex applies to itself.

    macOS refuses to apply a seatbelt profile inside another one, so Codex keeps
    its own sandbox only while the controller does not confine it. Otherwise the
    controller-owned profile is the single confinement authority.
    """
    return native if isolation.empty else "danger-full-access"


def describe_isolation(isolation: Isolation) -> IsolationEvidence:
    return isolation_evidence(
        isolation,
        "codex-workspace-write" if isolation.empty else "macos-sandbox-exec",
    )


def run_implementer(
    *,
    worktree: Path,
    prompt: str,
    out_dir: Path,
    model: str,
    effort: str,
    timeout_seconds: int,
    isolation: Isolation = Isolation(),
) -> CodexActuation:
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
            worktree, _sandbox(isolation, "workspace-write"), model, effort
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

    reported = _events(events)
    return UnsignedActuation(
        exit_code=completed.returncode,
        duration_ms=int((time.monotonic() - started) * 1000),
        native=_native_profile(model, effort),
        observed=_observed(reported),
        usage=_usage(reported),
        events_path=str(events),
        events_sha256=sha256_file(events),
        stderr_path=str(stderr_path),
        stderr_sha256=sha256_file(stderr_path),
        last_message_path=str(last_message),
        last_message_sha256=(
            sha256_file(last_message) if last_message.is_file() else None
        ),
    ).signed()


def run_reviewer(
    *,
    worktree: Path,
    prompt: str,
    schema_path: Path,
    out_dir: Path,
    model: str,
    effort: str,
    timeout_seconds: int,
    isolation: Isolation = Isolation(),
) -> tuple[dict[str, Any], CodexReviewMeta]:
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
            worktree, _sandbox(isolation, "read-only"), model, effort
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

    reported = _events(stdout_path)
    meta = UnsignedReviewMeta(
        exit_code=completed.returncode,
        duration_ms=int((time.monotonic() - started) * 1000),
        native=_native_profile(model, effort),
        # Read from the event stream `--json` produces, under the names the
        # stream uses, and never from the keys the command line carried.
        observed=_observed(reported),
        usage=_usage(reported),
        stdout_path=str(stdout_path),
        stdout_sha256=sha256_file(stdout_path),
        stderr_path=str(stderr_path),
        stderr_sha256=sha256_file(stderr_path),
        result_path=str(result_path),
        result_sha256=(
            sha256_file(result_path) if result_path.is_file() else None
        ),
    ).signed()
    if completed.returncode != 0:
        raise CodexError(f"reviewer exited with {completed.returncode}; see {stderr_path}")
    try:
        return json.loads(result_path.read_text(encoding="utf-8")), meta
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CodexError(f"invalid reviewer output: {result_path}") from exc
