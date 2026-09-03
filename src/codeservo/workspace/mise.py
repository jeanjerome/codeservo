"""The mise execution provider.

Everything the controller knows about mise lives here: the commands it runs,
what it reads back from them, and the command a task gate becomes. Every fact
below was measured on mise 2026.9.1 on 2026-09-03 rather than read.

mise keeps every tool it installs in one data directory, `MISE_DATA_DIR`,
outside the tree it measures. The controller therefore hands it a directory
of its own, under the state directory, installs there once before the
baseline, and both trees measure through it: what a measurement runs on is
the controller's and never the operator's.

`mise lock` writes the lockfile and must never run inside a run. There is no
one command that answers whether the lockfile agrees with the manifest:
`mise install --dry-run-code` says whether every tool is installed, and a
manifest that moved past its lockfile is read here from the two files, the
specifier the manifest declares against the specifiers the lockfile pinned a
version for. `MISE_LOCKED` then forbids resolving anything the lockfile does
not pin, and `MISE_OFFLINE` forbids every request.

mise installs a missing tool on the way to running anything, from four
different settings, and reads every config file up the directory tree and the
operator's own. Each of those is turned off, per command, by the variables
`measurement_environment` names: a task reads one manifest, the one the
constitution declares, and nothing the operator keeps.

A task inherits the environment it is started from, so what the controller
sets around a gate reaches the task: the location a document is written to,
and the location of a frozen sensor.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .provider import Environment, Installation, ProviderError, quote

PROVIDER = "mise"
LOCKFILE = "mise.lock"

# mise declares no named environments; the one a constitution may name.
DEFAULT_ENVIRONMENT = "default"

# Where mise keeps installed tools under its data directory.
INSTALLS_DIR = "installs"

# The one configuration file mise reads beside the manifest when told to read
# the manifest alone is none; this is the file that would override it, and
# whose appearance during a run is a change.
LOCAL_CONFIG_SUFFIX = ".local.toml"

# What the record carries for a self-description the provider did not make.
UNREPORTED = "unknown"

# What forbids a measurement from resolving or installing: mise installs a
# missing tool on the way to `run`, `exec` and the not-found handler unless
# each is told not to, and reaches the network unless told not to.
MEASUREMENT_ENVIRONMENT = {
    "MISE_OFFLINE": "1",
    "MISE_AUTO_INSTALL": "false",
    "MISE_EXEC_AUTO_INSTALL": "false",
    "MISE_NOT_FOUND_AUTO_INSTALL": "false",
    "MISE_TASK_RUN_AUTO_INSTALL": "false",
}

_DESCRIPTION_TIMEOUT_SECONDS = 60
_INVENTORY_TIMEOUT_SECONDS = 120
_INSTALL_TIMEOUT_SECONDS = 1800


def _specifiers(declared: Any) -> list[str]:
    """The version specifiers one manifest entry declares for a tool."""
    if isinstance(declared, str):
        return [declared]
    if isinstance(declared, list):
        return [item for item in declared if isinstance(item, str)]
    if isinstance(declared, dict) and isinstance(declared.get("version"), str):
        return [declared["version"]]
    return []


def lock_disagreements(manifest_text: str, lock_text: str) -> list[str]:
    """Where the lockfile no longer pins what the manifest declares.

    A tool the manifest names that the lockfile has no entry for, or a
    specifier the lockfile pinned no version for, means a `mise install`
    without `MISE_LOCKED` would resolve something new: what would be measured
    is not what was frozen.
    """
    try:
        manifest = tomllib.loads(manifest_text)
        lock = tomllib.loads(lock_text)
    except tomllib.TOMLDecodeError as exc:
        return [f"the manifest or the lockfile is not readable as TOML: {exc}"]
    tools = manifest.get("tools")
    locked = lock.get("tools")
    tools = tools if isinstance(tools, dict) else {}
    locked = locked if isinstance(locked, dict) else {}
    statements: list[str] = []
    for name, declared in tools.items():
        entries = locked.get(name)
        entries = entries if isinstance(entries, list) else []
        pinned: set[str] = set()
        for entry in entries:
            if isinstance(entry, dict):
                pinned.update(
                    item
                    for item in entry.get("specifiers", [])
                    if isinstance(item, str)
                )
        if not entries:
            statements.append(f"{name} is declared and not in the lockfile")
            continue
        statements.extend(
            f"{name}@{specifier} is not in the lockfile"
            for specifier in _specifiers(declared)
            if specifier not in pinned
        )
    return statements


def _only_default(environment: str) -> None:
    """Refuse any environment but the one a mise manifest declares.

    mise has one toolchain per manifest and no named environments, so the
    constitution's `environment` can only be the default; a run declaring
    another one names something no command here could select.
    """
    if environment != DEFAULT_ENVIRONMENT:
        raise ProviderError(
            f"execution environment: {PROVIDER} declares no environment"
            f" {environment}; declare {DEFAULT_ENVIRONMENT} or nothing"
        )


class Mise:
    """The mise provider, behind the port every provider answers."""

    name = PROVIDER
    lockfile = LOCKFILE
    shared_installs = True

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)

    # --- the environment every mise command runs under ------------------------

    def _empty_config(self) -> Path:
        """An empty configuration, standing in for the operator's own files.

        mise refuses a path that is not a TOML file where a config file is
        expected, so the file exists and says nothing.
        """
        path = self.data_dir / "config" / "empty.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("", encoding="utf-8")
        return path

    def _isolation(self, manifest: Path) -> dict[str, str]:
        """What keeps every mise command to one manifest and one directory.

        The manifest is read by its own name and no other, the search up the
        tree stops above it, the operator's global and system files are
        replaced by an empty one, the directory holding it is trusted without
        touching the operator's trust store, and every tool lives under the
        controller's data directory.
        """
        empty = str(self._empty_config())
        return {
            "MISE_DATA_DIR": str(self.data_dir),
            "MISE_CACHE_DIR": str(self.data_dir / "cache"),
            "MISE_GLOBAL_CONFIG_FILE": empty,
            "MISE_SYSTEM_CONFIG_FILE": empty,
            "MISE_OVERRIDE_CONFIG_FILENAMES": manifest.name,
            "MISE_CEILING_PATHS": str(manifest.parent.parent),
            "MISE_TRUSTED_CONFIG_PATHS": str(manifest.parent),
            "MISE_YES": "1",
            "MISE_LOCKED": "1",
        }

    def measurement_environment(self, manifest: Path) -> dict[str, str]:
        """The variables every measurement process runs under."""
        return {**self._isolation(manifest), **MEASUREMENT_ENVIRONMENT}

    # --- the two locations the controller watches ----------------------------------

    def provider_directory(self, manifest: Path) -> Path:  # noqa: ARG002
        """The directory holding every tool a measurement runs on.

        It is the controller's, whichever tree the manifest lies in: the
        argument is what the port passes, and the answer does not read it.
        """
        return self.data_dir

    def config_path(self, manifest: Path) -> Path:
        """The file that would override the manifest, and is told to be ignored.

        It is still watched: a candidate writing one is a change to what was
        frozen, whether or not mise would have read it.
        """
        return manifest.with_name(manifest.stem + LOCAL_CONFIG_SUFFIX)

    # --- the commands -------------------------------------------------------------

    def version_command(self) -> list[str]:
        return [PROVIDER, "version", "--json"]

    def tasks_command(self, manifest: Path) -> list[str]:
        return [PROVIDER, "tasks", "ls", "--json", "-C", str(manifest.parent)]

    def inventory_command(self, manifest: Path) -> list[str]:
        return [PROVIDER, "ls", "--json", "--current", "-C", str(manifest.parent)]

    def install_command(self, manifest: Path) -> list[str]:
        """The command that makes every pinned tool exist in the data directory.

        `MISE_LOCKED` in the environment is what forbids resolution: a tool
        the lockfile does not pin a URL for is refused rather than resolved.
        What it may still do is fetch what the lockfile already pins.
        """
        return [PROVIDER, "install", "-C", str(manifest.parent)]

    def task_command(
        self,
        *,
        manifest: Path,
        environment: str,
        task: str,
        arguments: Iterable[str] = (),
    ) -> str:
        """The command one task gate runs against the tree it measures.

        `-q` drops the line mise would otherwise print before the task's own
        output, so what reaches the record is what the tool printed. Arguments
        follow `--`, where mise passes them to the task's command.
        """
        _only_default(environment)
        command = [
            PROVIDER,
            "run",
            "-q",
            "-C",
            quote(str(manifest.parent)),
            quote(task),
        ]
        quoted = [quote(argument) for argument in arguments]
        if quoted:
            command.extend(["--", *quoted])
        return " ".join(command)

    # --- running them ----------------------------------------------------------------

    def _capture(
        self, command: list[str], manifest: Path, timeout_seconds: int, *, measure: bool
    ) -> subprocess.CompletedProcess:
        variables = os.environ.copy()
        variables.update(
            self.measurement_environment(manifest)
            if measure
            else self._isolation(manifest)
        )
        try:
            return subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                env=variables,
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

    def _diagnostic(self, completed: subprocess.CompletedProcess) -> str:
        for line in (completed.stderr or completed.stdout or "").splitlines():
            if line.strip():
                return line.strip()
        return f"exit code {completed.returncode}"

    def _json(self, completed: subprocess.CompletedProcess, what: str) -> Any:
        if completed.returncode != 0:
            raise ProviderError(
                f"execution environment: {PROVIDER} {what} failed: {self._diagnostic(completed)}"
            )
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"execution environment: {PROVIDER} {what} answered no readable JSON: {exc}"
            ) from exc

    def _description(self, manifest: Path) -> tuple[str, str]:
        document = self._json(
            self._capture(
                self.version_command(),
                manifest,
                _DESCRIPTION_TIMEOUT_SECONDS,
                measure=True,
            ),
            "version",
        )
        if not isinstance(document, dict):
            raise ProviderError(
                f"execution environment: {PROVIDER} described itself as no object"
            )
        version = document.get("version")
        system = document.get("os")
        arch = document.get("arch")
        platform = (
            f"{system}-{arch}"
            if isinstance(system, str) and isinstance(arch, str)
            else UNREPORTED
        )
        return (
            version.strip()
            if isinstance(version, str) and version.strip()
            else UNREPORTED
        ), platform

    def _tasks(self, manifest: Path) -> tuple[str, ...]:
        document = self._json(
            self._capture(
                self.tasks_command(manifest),
                manifest,
                _DESCRIPTION_TIMEOUT_SECONDS,
                measure=True,
            ),
            "tasks ls",
        )
        if not isinstance(document, list):
            raise ProviderError(
                f"execution environment: {PROVIDER} named no list of tasks"
            )
        return tuple(
            sorted(
                item["name"]
                for item in document
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            )
        )

    def _inventory(self, manifest: Path, lock_path: str) -> list:
        """What the lockfile pins, tool by tool, as mise lists it.

        mise lists a tool the lockfile does not pin under a warning rather than
        an error, and lists the others as if nothing were wrong; the warning is
        read, because a silent omission would freeze less than was declared.
        """
        completed = self._capture(
            self.inventory_command(manifest),
            manifest,
            _INVENTORY_TIMEOUT_SECONDS,
            measure=True,
        )
        if "not in the lockfile" in (completed.stderr or ""):
            raise ProviderError(
                f"execution environment: {lock_path} is not consistent with the"
                f" manifest it locks: {self._diagnostic(completed)}"
            )
        document = self._json(completed, "ls")
        if not isinstance(document, dict):
            raise ProviderError(f"execution environment: {PROVIDER} listed no tools")
        packages: list[dict[str, Any]] = []
        for tool, entries in sorted(document.items()):
            for entry in entries if isinstance(entries, list) else []:
                if not isinstance(entry, dict):
                    continue
                packages.append(
                    {
                        "name": tool,
                        "version": entry.get("version"),
                        "requested": entry.get("requested_version"),
                        "installed": bool(entry.get("installed")),
                    }
                )
        return packages

    def freeze(
        self,
        *,
        manifest: Path,
        lock_path: str,
        environment: str,
        tasks: Iterable[str],
    ) -> Environment:
        """Resolve the pinned toolchain, or refuse to measure through it.

        The verdict comes first and is read from the two files: a manifest
        that moved past its lockfile ends the run before mise is even asked.
        """
        _only_default(environment)
        lock = (
            manifest.parent / lock_path
            if not Path(lock_path).is_absolute()
            else Path(lock_path)
        )
        disagreements = lock_disagreements(
            manifest.read_text(encoding="utf-8"), lock.read_text(encoding="utf-8")
        )
        if disagreements:
            raise ProviderError(
                f"execution environment: {lock_path} is not consistent with the"
                f" manifest it locks: {'; '.join(disagreements)}"
            )
        packages = self._inventory(manifest, lock_path)
        version, platform = self._description(manifest)
        declared = self._tasks(manifest)
        missing = sorted(set(tasks) - set(declared))
        if missing:
            raise ProviderError(
                f"execution environment: {manifest.name} declares no task {', '.join(missing)}"
            )
        return Environment(
            version=version,
            platform=platform,
            tasks=declared,
            packages=packages,
            prefix=str(self.data_dir / INSTALLS_DIR),
        )

    def install(self, *, manifest: Path, environment: str) -> Installation:
        """Make every pinned tool exist in the controller's directory.

        Not offline: what the lockfile pins may have to be fetched. Still
        locked, so nothing the lockfile does not pin is resolved.
        """
        _only_default(environment)
        command = self.install_command(manifest)
        started = time.monotonic()
        completed = self._capture(
            command, manifest, _INSTALL_TIMEOUT_SECONDS, measure=False
        )
        return Installation(
            prefix_path=str(self.data_dir / INSTALLS_DIR),
            command=tuple(command),
            exit_code=completed.returncode,
            duration_ms=int((time.monotonic() - started) * 1000),
            diagnostic=self._diagnostic(completed),
        )
