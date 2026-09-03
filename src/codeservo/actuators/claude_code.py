from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, fields
from math import isfinite
from pathlib import Path
from typing import Any

from ..domain.document import UNSET, Document, Unset
from ..evidence.digests import sha256_file, sha256_record, sha256_text
from ..runtime.sandbox import (
    Isolation,
    IsolationEvidence,
    isolation_evidence,
    seatbelt_command,
)
from .base import ActuatorError, Billed, ObservedProfile, Tokens, Usage

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

# Where the result event of both roles reports what the session consumed: one
# block per model it billed, under the model's full identifier, and the cache
# durations the session wrote with, which the per-model blocks do not split.
USAGE_FIELD = "usage"
MODEL_USAGE_FIELD = "modelUsage"
CACHE_CREATION_FIELD = "cache_creation"
CACHE_DURATIONS = {
    "ephemeral_5m_input_tokens": "5m",
    "ephemeral_1h_input_tokens": "1h",
}
MIXED_DURATIONS = "mixed"


@dataclass(frozen=True, kw_only=True)
class Session(Document):
    """What the result event says about the session that just ended.

    A stream that produced no result event carries no session, and the block
    is empty rather than filled with nulls, so every field starts unset.
    """

    session_id: str | None | Unset = UNSET
    subtype: str | None | Unset = UNSET
    is_error: bool | Unset = UNSET
    num_turns: int | None | Unset = UNSET
    total_cost_usd: float | None | Unset = UNSET
    terminal_reason: str | None | Unset = UNSET

    @property
    def reported_an_error(self) -> bool:
        """Whether the session that ended said it failed.

        A stream that carried no result event said nothing about failing,
        which is not the same as saying it did not fail.
        """
        return self.is_error is True


@dataclass(frozen=True, kw_only=True)
class UnsignedActuation(Document):
    """One actuation, before it closes over itself."""

    command: tuple[str, ...]
    exit_code: int
    duration_ms: int
    session: Session
    usage: Usage
    native: dict[str, Any]
    observed: ObservedProfile
    events_path: str
    events_sha256: str
    stderr_path: str
    stderr_sha256: str
    last_message_path: str
    last_message_sha256: str

    def signed(self) -> ClaudeActuation:
        """This actuation, closed over the digest of what it holds."""
        carried = {
            declared.name: getattr(self, declared.name)
            for declared in fields(UnsignedActuation)
        }
        return ClaudeActuation(
            **carried, result_sha256=sha256_record(self.to_document())
        )


@dataclass(frozen=True, kw_only=True)
class ClaudeActuation(UnsignedActuation):
    """One actuation, and the digest recomputable from what it holds."""

    result_sha256: str


@dataclass(frozen=True, kw_only=True)
class UnsignedReviewMeta(Document):
    """One review call, before it closes over itself."""

    command: tuple[str, ...]
    exit_code: int
    duration_ms: int
    schema_sha256: str
    session: Session
    usage: Usage
    native: dict[str, Any]
    observed: ObservedProfile
    stdout_path: str
    stdout_sha256: str
    stderr_path: str
    stderr_sha256: str
    result_path: str
    result_sha256: str

    def signed(self) -> ClaudeReviewMeta:
        """This review call, closed over the digest of what it holds."""
        carried = {
            declared.name: getattr(self, declared.name)
            for declared in fields(UnsignedReviewMeta)
        }
        return ClaudeReviewMeta(
            **carried, meta_sha256=sha256_record(self.to_document())
        )


@dataclass(frozen=True, kw_only=True)
class ClaudeReviewMeta(UnsignedReviewMeta):
    """One review call, and the digest recomputable from what it holds."""

    meta_sha256: str


class ClaudeCodeError(ActuatorError):
    pass


def _base_command(*, model: str, tools: str, effort: str) -> list[str]:
    """The command both roles start from, carrying the profile unchanged.

    The model is its complete identifier and the effort one of the four the
    catalogue names. Whether the model accepts that effort is the CLI's to
    decide, and it fails explicitly when it does not.
    """
    return [
        "claude",
        *HERMETIC_FLAGS,
        # Confinement comes from the controller-owned seatbelt profile, so the
        # agent never nests a second sandbox of its own.
        "--permission-mode",
        "bypassPermissions",
        "--tools",
        tools,
        "--model",
        model,
        "--effort",
        effort,
    ]


def _native_profile(model: str, effort: str) -> dict:
    """The two flags the command carried, and their values."""
    return {"--model": model, "--effort": effort}


def _implementer_command(*, model: str, effort: str) -> tuple[list[str], dict]:
    command = _base_command(model=model, tools=IMPLEMENTER_TOOLS, effort=effort)
    command.extend(["--output-format", "stream-json", "--verbose"])
    return command, _native_profile(model, effort)


def _reviewer_command(
    *, model: str, effort: str, schema_path: Path
) -> tuple[list[str], dict]:
    """The reviewer command: the same profile flags, on read-only tools."""
    command = _base_command(model=model, tools=REVIEWER_TOOLS, effort=effort)
    command.extend(
        ["--output-format", "json", "--json-schema", _inline_schema(schema_path)]
    )
    return command, _native_profile(model, effort)


def _inline_schema(schema_path: Path) -> str:
    """Render the frozen review schema as Claude Code accepts it.

    The CLI validates the schema with a draft-07 validator that rejects the
    2020-12 `$schema` reference, so the dialect declaration is dropped.
    """
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
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


# A stream is another program's output, so what a field carries is whatever
# that program wrote there. The three readers below take a field only where it
# carries what the record declares for it. Anything else is not a measurement
# this record can hold, and reporting nothing is the honest answer: a consumer
# reading the record by its declared shape would otherwise find a mapping
# where a count belongs.


def _text(event: dict, field: str) -> str | None:
    value = event.get(field)
    return value if isinstance(value, str) else None


def _count(event: dict, field: str) -> int | None:
    value = event.get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _amount(event: dict, field: str) -> float | None:
    """One number of the stream, where JSON can carry it back.

    `json.loads` reads `NaN`, `Infinity` and `-Infinity`; `json.dumps` writes
    them back as literals no other JSON reader accepts. A record carrying one
    would state a number that JSON has no way to state.
    """
    value = event.get(field)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        amount = float(value)
    except OverflowError:
        return None
    return amount if isfinite(amount) else None


def _session_model(events: list[dict]) -> str | None:
    """The model the implementer's init event names, when the stream has one."""
    init = next(
        (
            event
            for event in events
            if event.get("type") == "system" and event.get("subtype") == "init"
        ),
        None,
    )
    return _text(init, "model") if init else None


def _cache_write_duration(result: dict | None) -> str | None:
    """The one duration the session wrote its cache with, if it wrote with one.

    The per-model blocks count cache writes without saying how long for; the
    session block says how many tokens went to each duration. A session that
    wrote with both leaves the duration mixed, which no single price rates.
    """
    usage = (result or {}).get(USAGE_FIELD)
    creation = usage.get(CACHE_CREATION_FIELD) if isinstance(usage, dict) else None
    if not isinstance(creation, dict):
        return None
    written = [
        duration
        for field, duration in CACHE_DURATIONS.items()
        if (_count(creation, field) or 0) > 0
    ]
    if not written:
        return None
    return written[0] if len(written) == 1 else MIXED_DURATIONS


def _usage(events: list[dict]) -> Usage:
    """What the session consumed, under each model it billed.

    Claude Code names every model that spent tokens, with the five counts and
    the list-price cost it computed itself. Its input count is the uncached
    input alone, and the cache reads and writes stand apart, so each count
    goes under the same name here. The cost it reports is kept beside the
    tokens as what the backend said, never as what the controller rates.
    """
    result = _result_event(events)
    reported = (result or {}).get(MODEL_USAGE_FIELD)
    billed: list[Billed] = []
    if isinstance(reported, dict):
        for name, record in reported.items():
            if not isinstance(record, dict):
                continue
            billed.append(
                Billed(
                    model=name,
                    tokens=Tokens(
                        input=_count(record, "inputTokens"),
                        cached_input=_count(record, "cacheReadInputTokens"),
                        cache_write=_count(record, "cacheCreationInputTokens"),
                        output=_count(record, "outputTokens"),
                        reasoning=_count(record, "thinkingTokens"),
                    ),
                    reported_cost_usd=_amount(record, "costUSD"),
                )
            )
    return Usage(billed=tuple(billed), cache_write_duration=_cache_write_duration(result))


def _session(result: dict | None) -> Session:
    if result is None:
        return Session()
    return Session(
        session_id=_text(result, "session_id"),
        subtype=_text(result, "subtype"),
        is_error=bool(result.get("is_error")),
        num_turns=_count(result, "num_turns"),
        total_cost_usd=_amount(result, "total_cost_usd"),
        terminal_reason=_text(result, "terminal_reason"),
    )


def _reported_model(events: list[dict]) -> str | None:
    """The model the session says the work ran on.

    The implementer's stream opens with an init event naming the model it
    ran on. The reviewer answers in a single result object with no such
    event, so its model comes from the usage record, which names every model
    the session billed: one name is a report, several are a choice, and
    choosing between them would be an inference rather than a report.
    """
    named = _session_model(events)
    if named:
        return named
    billed = [item.model for item in _usage(events).billed if item.model]
    return billed[0] if len(billed) == 1 else None


def _observed(events: list[dict]) -> ObservedProfile:
    """The inference profile the session reported about itself.

    Claude Code names its model and carries no reasoning effort in any event
    of either role, so `effort` stays empty. Nothing is put in its place: the
    requested value would repeat the request.
    """
    return ObservedProfile(model=_reported_model(events), effort=None)


def describe_isolation(isolation: Isolation) -> IsolationEvidence:
    return isolation_evidence(isolation, "macos-sandbox-exec")


def run_implementer(
    *,
    worktree: Path,
    prompt: str,
    out_dir: Path,
    model: str,
    effort: str,
    timeout_seconds: int,
    isolation: Isolation = Isolation(),
) -> ClaudeActuation:
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / "events.jsonl"
    stderr_path = out_dir / "stderr.log"
    last_message = out_dir / "last-message.md"
    started = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="codeservo-agent-") as temp:
        temporary_dir = Path(temp)
        temporary_events = temporary_dir / "events.jsonl"
        temporary_stderr = temporary_dir / "stderr.log"
        command, native = _implementer_command(model=model, effort=effort)

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

    record = UnsignedActuation(
        command=tuple(command),
        exit_code=completed.returncode,
        duration_ms=int((time.monotonic() - started) * 1000),
        session=_session(result),
        usage=_usage(events),
        native=native,
        observed=_observed(events),
        events_path=str(events_path),
        events_sha256=sha256_file(events_path),
        stderr_path=str(stderr_path),
        stderr_sha256=sha256_file(stderr_path),
        last_message_path=str(last_message),
        last_message_sha256=sha256_text(message),
    ).signed()
    if completed.returncode == 0 and record.session.reported_an_error:
        raise ClaudeCodeError(
            f"implementer reported {record.session.subtype};"
            f" see {stderr_path}"
        )
    return record


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
) -> tuple[dict[str, Any], ClaudeReviewMeta]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = out_dir / "stdout.log"
    stderr_path = out_dir / "stderr.log"
    result_path = out_dir / "review.json"
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
        command, native = _reviewer_command(
            model=model, effort=effort, schema_path=schema_path
        )
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
    meta = UnsignedReviewMeta(
        command=tuple(command),
        exit_code=completed.returncode,
        duration_ms=int((time.monotonic() - started) * 1000),
        schema_sha256=sha256_file(schema_path),
        session=_session(_result_event(_events(stdout_path))),
        usage=_usage(_events(stdout_path)),
        native=native,
        observed=_observed(_events(stdout_path)),
        stdout_path=str(stdout_path),
        stdout_sha256=sha256_file(stdout_path),
        stderr_path=str(stderr_path),
        stderr_sha256=sha256_file(stderr_path),
        result_path=str(result_path),
        result_sha256=sha256_file(result_path),
    ).signed()
    return review, meta


def _review_result(stdout_path: Path, stderr_path: Path) -> dict:
    # Bytes that are not UTF-8 are not a review either, and a decoder raising
    # through this call would end the run without a decision.
    try:
        payload = json.loads(stdout_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
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
