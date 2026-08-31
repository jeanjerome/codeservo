"""Test-only composition helpers for an already active macOS seatbelt."""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from functools import lru_cache
from unittest.mock import patch


NESTED_TEST_ENV = "CODESERVO_TEST_NESTED_SEATBELT"


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


@contextmanager
def controller_test_isolation():
    """Exercise controller logic without nesting seatbelt in a confined gate.

    Direct test execution keeps the production confinement intact. Only a test
    process that positively observes macOS rejecting a second profile uses the
    passthrough functions, and those patches never reach production modules.
    """

    if not already_confined():
        yield False
        return

    with ExitStack() as stack:
        stack.enter_context(
            patch("codeservo.process.seatbelt_command", _without_additional_seatbelt)
        )
        stack.enter_context(
            patch("codeservo.codex.seatbelt_command", _without_additional_seatbelt)
        )
        stack.enter_context(
            patch(
                "codeservo.claude_code.seatbelt_command",
                _without_additional_seatbelt,
            )
        )
        stack.enter_context(patch.dict(os.environ, {NESTED_TEST_ENV: "1"}))
        yield True
