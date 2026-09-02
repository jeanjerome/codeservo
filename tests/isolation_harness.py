"""Test-only composition helpers for an already active macOS seatbelt."""

from __future__ import annotations

import fcntl
from contextlib import ExitStack, contextmanager
from functools import lru_cache
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch


NESTED_TEST_ENV = "CODESERVO_TEST_NESTED_SEATBELT"
GATE_RECORD_ENV = "CODESERVO_TEST_GATE_RECORD"


@lru_cache(maxsize=1)
def nested_seatbelt_exit_code() -> int:
    if sys.platform != "darwin":
        return 0
    completed = subprocess.run(
        [
            "/usr/bin/sandbox-exec",
            "-p",
            "(version 1)(allow default)",
            "/usr/bin/true",
        ],
        capture_output=True,
        check=False,
    )
    return completed.returncode


def already_confined() -> bool:
    """Whether macOS refused a second seatbelt application with EX_OSERR."""
    return nested_seatbelt_exit_code() == os.EX_OSERR


def _without_additional_seatbelt(command: list[str], _isolation: object) -> list[str]:
    return command


def _descriptor_path(descriptor: int) -> Path | None:
    try:
        raw = fcntl.fcntl(descriptor, fcntl.F_GETPATH, bytes(1024))
    except OSError:
        return None
    value = raw.split(bytes(1), 1)[0]
    return Path(value.decode()) if value else None


def protected_gate_record() -> Path:
    """Return a readable directory that the active outer profile protects."""

    if not already_confined():
        raise AssertionError("test process is not inside an existing seatbelt")

    candidates: list[Path] = []
    configured = os.environ.get(GATE_RECORD_ENV)
    if configured:
        candidates.append(Path(configured).resolve())
    for descriptor in (1, 2):
        path = _descriptor_path(descriptor)
        if path is not None:
            candidates.append(path.resolve().parent)
    # Gate-owned records keep priority. A read-only reviewer has no such
    # descriptor, but its current repository is itself a protected anchor.
    candidates.append(Path.cwd().resolve())

    for candidate in candidates:
        try:
            tuple(candidate.iterdir())
        except OSError:
            continue
        probe = candidate / f"codeservo-test-write-probe-{os.getpid()}"
        try:
            probe.write_text("forbidden\n", encoding="utf-8")
        except OSError:
            if probe.exists():
                raise AssertionError(f"failed write left a file behind: {probe}")
            return candidate
        else:
            probe.unlink()

    raise AssertionError("no readable, write-protected gate record was found")


@contextmanager
def controller_test_isolation():
    """Exercise controller logic without nesting seatbelt in a confined gate.

    Direct test execution keeps the production confinement intact. Only a test
    process that positively observes macOS rejecting a second profile uses the
    passthrough functions, and those patches never reach production modules.
    """

    if not already_confined():
        with patch.dict(
            os.environ,
            {
                NESTED_TEST_ENV: "",
                GATE_RECORD_ENV: "",
            },
        ):
            yield False
        return

    gate_record = protected_gate_record()
    with ExitStack() as stack:
        stack.enter_context(
            patch("codeservo.runtime.process.seatbelt_command", _without_additional_seatbelt)
        )
        stack.enter_context(
            patch("codeservo.actuators.codex.seatbelt_command", _without_additional_seatbelt)
        )
        stack.enter_context(
            patch(
                "codeservo.actuators.claude_code.seatbelt_command",
                _without_additional_seatbelt,
            )
        )
        stack.enter_context(
            patch.dict(
                os.environ,
                {
                    NESTED_TEST_ENV: "1",
                    GATE_RECORD_ENV: str(gate_record),
                },
            )
        )
        yield True
