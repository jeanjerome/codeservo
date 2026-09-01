"""The pixi execution provider.

Everything the controller knows about pixi lives here: the commands it runs,
what it reads back from them, and the command a task gate becomes.

Two provider facts govern this module.

`pixi lock --check` is unusable inside a run: on a workspace whose lockfile
disagrees with the manifest it exits non-zero *and rewrites the lockfile*,
mutating the control input it reports as stale. `pixi list --locked
--no-install` returns the same verdict and the inventory in one command,
writes neither file, and installs nothing.

An unknown task is not an error: `pixi run` executes an unrecognized name as a
program, so a mistyped task silently becomes a different measurement. The task
set an environment declares is therefore read from `pixi info` and checked
before any task runs.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

PROVIDER = "pixi"
LOCKFILE = "pixi.lock"

# The inventory resolves a lockfile; the description reads two files.
_INVENTORY_TIMEOUT_SECONDS = 120
_DESCRIPTION_TIMEOUT_SECONDS = 60


class ProviderError(RuntimeError):
    """A provider fact that ends a run before anything is measured."""


@dataclass(frozen=True)
class Environment:
    """What the provider resolved for one declared environment."""

    version: str
    platform: str
    tasks: tuple[str, ...]
    packages: list


def _quote(value: str) -> str:
    """Quote one value the constitution supplied.

    Gate commands reach a shell. Task, environment and manifest are validated
    when the constitution is parsed, so nothing needing an escape survives
    that far; quoting here means the property holds by construction rather
    than by that validation staying correct.
    """
    return "'" + value.replace("'", "'\\''") + "'"


def inventory_command(manifest: Path) -> list[str]:
    """The consistency verdict and the inventory, in one command.

    It never writes the manifest, never writes the lockfile, and installs
    nothing. A non-zero exit means the lockfile disagrees with the manifest.
    """
    return [
        PROVIDER,
        "list",
        "--json",
        "--locked",
        "--no-install",
        "--no-config",
        "--manifest-path",
        str(manifest),
    ]


def description_command(manifest: Path) -> list[str]:
    """What the provider says about itself and about the declared environments.

    A description and never a verdict: it exits zero whether or not the
    lockfile agrees with the manifest, so its exit status is not read.
    """
    return [
        PROVIDER,
        "info",
        "--json",
        "--no-config",
        "--manifest-path",
        str(manifest),
    ]


def task_command(*, manifest: Path, environment: str, task: str) -> str:
    """The command one task gate runs against the tree it measures.

    `--as-is` is the documented shorthand for `--no-install --frozen`, so a
    gate never installs and never resolves; `--clean-env` and `--no-config`
    keep the operator's shell and configuration out of the measurement.
    """
    return " ".join(
        [
            PROVIDER,
            "run",
            "--as-is",
            "--clean-env",
            "--no-config",
            "--manifest-path",
            _quote(str(manifest)),
            "--environment",
            _quote(environment),
            _quote(task),
        ]
    )


def _capture(command: list[str], timeout_seconds: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except OSError as exc:
        raise ProviderError(
            f"execution environment: cannot run {PROVIDER} {command[1]}: {exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ProviderError(
            f"execution environment: {PROVIDER} {command[1]} did not answer"
            f" within {timeout_seconds}s"
        ) from exc


def _diagnostic(completed: subprocess.CompletedProcess) -> str:
    for line in (completed.stderr or completed.stdout or "").splitlines():
        if line.strip():
            return line.strip()
    return f"exit code {completed.returncode}"


def _inventory(manifest: Path, lock_path: str) -> list:
    completed = _capture(inventory_command(manifest), _INVENTORY_TIMEOUT_SECONDS)
    if completed.returncode != 0:
        raise ProviderError(
            f"execution environment: {lock_path} is not consistent with the"
            f" manifest it locks: {_diagnostic(completed)}"
        )
    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ProviderError(
            f"execution environment: {lock_path} resolved to no readable"
            f" inventory: {exc}"
        ) from exc
    if not isinstance(document, list):
        raise ProviderError(
            f"execution environment: {lock_path} resolved to no readable inventory"
        )
    return document


def _description(manifest: Path, environment: str) -> tuple[str, str, tuple[str, ...]]:
    completed = _capture(description_command(manifest), _DESCRIPTION_TIMEOUT_SECONDS)
    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ProviderError(
            f"execution environment: {PROVIDER} described no environment"
            f" of {manifest.name}: {exc}"
        ) from exc
    declared = {
        str(item.get("name")): tuple(
            sorted(str(task) for task in item.get("tasks", []))
        )
        for item in document.get("environments_info", [])
        if isinstance(item, dict)
    }
    if environment not in declared:
        raise ProviderError(
            f"execution environment: {PROVIDER} declares no environment"
            f" {environment}"
        )
    # Only these three facts are read. The cache directory, the credentials
    # location, the configuration locations and the global directories the
    # description also carries are the operator's, not the run's.
    return (
        str(document.get("version", "unknown")),
        str(document.get("platform", "unknown")),
        declared[environment],
    )


def freeze(
    *, manifest: Path, lock_path: str, environment: str, tasks: Iterable[str]
) -> Environment:
    """Resolve one declared environment, or refuse to measure through it.

    The verdict comes first: an inventory that cannot be resolved from the
    lockfile ends the run before the description is even asked for.
    """
    packages = _inventory(manifest, lock_path)
    version, platform, declared = _description(manifest, environment)
    missing = sorted(set(tasks) - set(declared))
    if missing:
        raise ProviderError(
            f"execution environment: environment {environment} declares no"
            f" task {', '.join(missing)}"
        )
    return Environment(
        version=version, platform=platform, tasks=declared, packages=packages
    )
