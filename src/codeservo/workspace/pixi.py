"""The pixi execution provider.

Everything the controller knows about pixi lives here: the commands it runs,
what it reads back from them, and the command a task gate becomes.

Three provider facts govern this module.

`pixi lock --check` is unusable inside a run: on a workspace whose lockfile
disagrees with the manifest it exits non-zero *and rewrites the lockfile*,
mutating the control input it reports as stale. `pixi list --locked
--no-install` returns the same verdict and the inventory in one command,
writes neither file, and installs nothing.

An unknown task is not an error: `pixi run` executes an unrecognized name as a
program, so a mistyped task silently becomes a different measurement. The task
set an environment declares is therefore read from `pixi info` and checked
before any task runs.

A missing environment is not an error either: without `--clean-env` a task runs
on whatever interpreter the operator's shell offers, and reports a result about
a tree it never used. `--clean-env` turns that silence into a failure, and
`pixi install --locked` is what makes the environment exist before anything is
measured through it.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .provider import Environment, Installation, ProviderError, quote

PROVIDER = "pixi"
LOCKFILE = "pixi.lock"

# What the record carries for a self-description the provider did not make.
UNREPORTED = "unknown"

# The directory the provider owns inside a workspace. The installed
# environments and the workspace-local configuration both live under it, so it
# is the whole of what a run measures through and never a measurement's output.
PROVIDER_DIR = Path(".pixi")

# The workspace-local provider configuration, relative to the manifest that
# declares the workspace. `--no-config` drops the user and system files and
# leaves this one, so it is a control input like the manifest and the lockfile.
CONFIG_FILE = PROVIDER_DIR / "config.toml"

# What forbids a measurement from resolving or installing. A plain `pixi run`
# installs the environment it needs; with these three set it installs nothing,
# fetches nothing and resolves nothing, so no gate can change the environment
# it is measured in.
MEASUREMENT_ENVIRONMENT = {
    "PIXI_OFFLINE": "true",
    "PIXI_NO_INSTALL": "true",
    "PIXI_FROZEN": "true",
}

# The inventory resolves a lockfile; the description reads two files.
_INVENTORY_TIMEOUT_SECONDS = 120
_DESCRIPTION_TIMEOUT_SECONDS = 60
# The installation may fetch every package the lockfile pins.
_INSTALL_TIMEOUT_SECONDS = 1800


@dataclass(frozen=True)
class Description:
    """What the provider says about itself and about one declared environment.

    Only these four facts are read. The cache directory, the credentials
    location, the configuration locations and the global directories the
    description also carries are the operator's, not the run's.
    """

    version: str
    platform: str
    tasks: tuple[str, ...]
    prefix: str


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


def install_command(*, manifest: Path, environment: str) -> list[str]:
    """The command that makes one declared environment exist.

    `--locked` is what forbids resolution: on a lockfile that disagrees with
    the manifest it exits non-zero, leaves the lockfile intact and creates no
    environment. What it may still do is fetch the packages that lockfile
    already pins.
    """
    return [
        PROVIDER,
        "install",
        "--locked",
        "--no-config",
        "--environment",
        environment,
        "--manifest-path",
        str(manifest),
    ]


def config_path(manifest: Path) -> Path:
    """The workspace-local provider configuration of one manifest.

    `--no-config` drops the operator's user and system configuration and reads
    this file, so what it says is part of what a measurement ran under.
    """
    return manifest.parent / CONFIG_FILE


def provider_directory(manifest: Path) -> Path:
    """The directory the provider owns in the workspace of one manifest.

    `.pixi/envs/<name>` holds the environment a measurement runs on and
    `.pixi/config.toml` what it runs under, so what a confinement protects is
    the directory rather than either of them.
    """
    return manifest.parent / PROVIDER_DIR


def measurement_environment() -> dict[str, str]:
    """The variables every measurement process runs under."""
    return dict(MEASUREMENT_ENVIRONMENT)


def task_command(
    *,
    manifest: Path,
    environment: str,
    task: str,
    arguments: Iterable[str] = (),
) -> str:
    """The command one task gate runs against the tree it measures.

    `--as-is` is the documented shorthand for `--no-install --frozen`, so a
    gate never installs and never resolves; `--clean-env` and `--no-config`
    keep the operator's shell and configuration out of the measurement.

    Arguments are appended after the task name, where the provider passes them
    on to the task's own command. This is the one channel a controller has
    into a task: `--clean-env` empties the environment the task starts with,
    so a variable set around this command does not reach it, and neither does
    one a manifest re-exports from it.
    """
    return " ".join(
        [
            PROVIDER,
            "run",
            "--as-is",
            "--clean-env",
            "--no-config",
            "--manifest-path",
            quote(str(manifest)),
            "--environment",
            quote(environment),
            quote(task),
            *(quote(argument) for argument in arguments),
        ]
    )


def _capture(
    command: list[str],
    timeout_seconds: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=env,
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


def _reported(document: dict, key: str) -> str:
    """One self-description of the provider, where it is a string.

    A value of another shape is not rendered into the record: `str()` of a
    mapping would state something the provider never said, and the field
    already has a word for what was not reported.
    """
    value = document.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else UNREPORTED


def read_description(
    stdout: str, *, manifest_name: str, environment: str
) -> Description:
    """Project what the provider printed about itself onto what a run reads.

    Everything below the JSON decoding is a shape the provider is trusted to
    hold and therefore a shape this refuses by name when it does not: a
    description that parses without being an object, an environment list that
    is not a list, or a task set that is not one. Reaching for a key on
    whatever `json.loads` returned would end the run in an interpreter
    traceback instead of a named refusal, in a controller whose whole business
    is closing a run with a decision.
    """
    refusal = (
        f"execution environment: {PROVIDER} described no environment of {manifest_name}"
    )
    try:
        document = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"{refusal}: {exc}") from exc
    if not isinstance(document, dict):
        raise ProviderError(f"{refusal}: the description is not an object")

    described = document.get("environments_info", [])
    if not isinstance(described, list):
        raise ProviderError(f"{refusal}: it names no list of environments")
    declared = {
        item.get("name"): item
        for item in described
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if environment not in declared:
        raise ProviderError(
            f"execution environment: {PROVIDER} declares no environment {environment}"
        )

    selected = declared[environment]
    prefix = _reported(selected, "prefix")
    if prefix == UNREPORTED:
        raise ProviderError(
            f"execution environment: {PROVIDER} reports no directory for"
            f" environment {environment}"
        )
    tasks = selected.get("tasks", [])
    if not isinstance(tasks, list):
        raise ProviderError(
            f"execution environment: {PROVIDER} names no task set for"
            f" environment {environment}"
        )
    return Description(
        version=_reported(document, "version"),
        platform=_reported(document, "platform"),
        tasks=tuple(sorted(task for task in tasks if isinstance(task, str))),
        prefix=prefix,
    )


def _description(manifest: Path, environment: str) -> Description:
    completed = _capture(description_command(manifest), _DESCRIPTION_TIMEOUT_SECONDS)
    return read_description(
        completed.stdout, manifest_name=manifest.name, environment=environment
    )


def environment_prefix(*, manifest: Path, environment: str) -> str:
    """The directory the provider itself reports for one environment.

    Asked of the provider rather than assembled from the manifest, so what the
    controller checks for and what the provider would create are the same
    location by construction.
    """
    return _description(manifest, environment).prefix


def _installing_environment() -> dict[str, str]:
    """The environment the installation runs under.

    The three measurement variables are dropped from what the controller
    inherited: `PIXI_NO_INSTALL` or `PIXI_FROZEN` coming from the operator's
    shell would turn the installation into a no-op that still exits zero, and
    `PIXI_OFFLINE` would forbid fetching what the lockfile pins.
    """
    environment = os.environ.copy()
    for variable in MEASUREMENT_ENVIRONMENT:
        environment.pop(variable, None)
    return environment


def install(*, manifest: Path, environment: str) -> Installation:
    """Make one declared environment exist, without resolving anything.

    The directory is read from the provider before the installation runs, so a
    failed installation still records the location it was refused for.
    """
    prefix = environment_prefix(manifest=manifest, environment=environment)
    command = install_command(manifest=manifest, environment=environment)
    started = time.monotonic()
    completed = _capture(
        command, _INSTALL_TIMEOUT_SECONDS, env=_installing_environment()
    )
    return Installation(
        prefix_path=prefix,
        command=tuple(command),
        exit_code=completed.returncode,
        duration_ms=int((time.monotonic() - started) * 1000),
        diagnostic=_diagnostic(completed),
    )


def freeze(
    *, manifest: Path, lock_path: str, environment: str, tasks: Iterable[str]
) -> Environment:
    """Resolve one declared environment, or refuse to measure through it.

    The verdict comes first: an inventory that cannot be resolved from the
    lockfile ends the run before the description is even asked for.
    """
    packages = _inventory(manifest, lock_path)
    described = _description(manifest, environment)
    missing = sorted(set(tasks) - set(described.tasks))
    if missing:
        raise ProviderError(
            f"execution environment: environment {environment} declares no"
            f" task {', '.join(missing)}"
        )
    return Environment(
        version=described.version,
        platform=described.platform,
        tasks=described.tasks,
        packages=packages,
        prefix=described.prefix,
    )


class Pixi:
    """The pixi provider, behind the port every provider answers.

    The module functions above are what the provider does; this object is how
    the controller holds it to the six operations without naming pixi.
    """

    name = PROVIDER
    lockfile = LOCKFILE
    # The environment lives under `.pixi` inside the tree it is measured in,
    # so the candidate is installed after the checkout and the source must
    # already carry its own.
    shared_installs = False

    def freeze(
        self,
        *,
        manifest: Path,
        lock_path: str,
        environment: str,
        tasks: Iterable[str],
    ) -> Environment:
        return freeze(
            manifest=manifest, lock_path=lock_path, environment=environment, tasks=tasks
        )

    def install(self, *, manifest: Path, environment: str) -> Installation:
        return install(manifest=manifest, environment=environment)

    def task_command(
        self,
        *,
        manifest: Path,
        environment: str,
        task: str,
        arguments: Iterable[str] = (),
    ) -> str:
        return task_command(
            manifest=manifest, environment=environment, task=task, arguments=arguments
        )

    def measurement_environment(self, manifest: Path) -> dict[str, str]:  # noqa: ARG002
        # The variables forbid resolving and installing whichever tree is
        # measured: the argument is what the port passes, and pixi reads none of it.
        return measurement_environment()

    def provider_directory(self, manifest: Path) -> Path:
        return provider_directory(manifest)

    def config_path(self, manifest: Path) -> Path:
        return config_path(manifest)
