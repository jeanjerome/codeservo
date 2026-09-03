"""The execution provider port: what a run asks of the tool that pins its toolchain.

A constitution may declare the provider its gates measure through. The
controller standardises what it asks of one, in six operations, and each
provider answers in its own commands: what the lockfile resolves to, what the
provider says of itself and of the tasks it declares, how the environment is
installed, the command one task gate becomes, the variables every measurement
runs under, and the two locations the controller must watch, the directory
holding the environment and the configuration file that could override the
manifest. Nothing of one provider's vocabulary reaches the controller.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# The provider a record names when a constitution declares none: the host's
# own environment, one command at a time.
NO_PROVIDER = "none"


class ProviderError(RuntimeError):
    """A provider fact that ends a run before anything is measured."""


@dataclass(frozen=True)
class Environment:
    """What the provider resolved for one declared environment."""

    version: str
    platform: str
    tasks: tuple[str, ...]
    packages: list
    prefix: str


@dataclass(frozen=True)
class Installation:
    """One environment installed where its provider keeps it."""

    prefix_path: str
    command: tuple[str, ...]
    exit_code: int
    duration_ms: int
    diagnostic: str


class Provider(Protocol):
    """The six operations a run asks of its execution provider.

    `name` is the word the constitution and the record use for it, and
    `lockfile` the file the provider keeps beside the manifest. A provider
    that installs into the tree it measures answers `shared_installs` false:
    the controller then installs into the candidate after the checkout. One
    that keeps every tool outside the tree answers true, and the controller
    installs into the directory it owns before the baseline, once for both
    trees.
    """

    @property
    def name(self) -> str: ...
    @property
    def lockfile(self) -> str: ...
    @property
    def shared_installs(self) -> bool: ...

    def freeze(
        self,
        *,
        manifest: Path,
        lock_path: str,
        environment: str,
        tasks: Iterable[str],
    ) -> Environment: ...

    def install(self, *, manifest: Path, environment: str) -> Installation: ...

    def task_command(
        self,
        *,
        manifest: Path,
        environment: str,
        task: str,
        arguments: Iterable[str] = (),
    ) -> str: ...

    def measurement_environment(self, manifest: Path) -> dict[str, str]: ...

    def provider_directory(self, manifest: Path) -> Path: ...

    def config_path(self, manifest: Path) -> Path: ...


def quote(value: str) -> str:
    """Quote one value the constitution supplied, for a command that reaches a shell.

    Task, environment and manifest are validated when the constitution is
    parsed, so nothing needing an escape survives that far; quoting here means
    the property holds by construction rather than by that validation staying
    correct.
    """
    return "'" + value.replace("'", "'\\''") + "'"


def provider_names() -> tuple[str, ...]:
    """The providers a constitution may declare, in the order they are listed."""
    from . import mise, pixi

    return (pixi.PROVIDER, mise.PROVIDER)


def lockfile_of(name: str) -> str:
    """The lockfile a provider keeps beside the manifest, by the provider's name."""
    from . import mise, pixi

    known = {pixi.PROVIDER: pixi.LOCKFILE, mise.PROVIDER: mise.LOCKFILE}
    if name not in known:
        raise ProviderError(f"unknown execution provider: {name}")
    return known[name]


def load_provider(name: str, state_root: Path) -> Provider:
    """The provider a constitution names, given where the controller keeps its own.

    A provider that keeps its tools outside the tree is handed the directory
    the controller owns for them, so what a measurement runs on is the
    controller's and never the operator's.
    """
    from . import mise, pixi

    if name == pixi.PROVIDER:
        return pixi.Pixi()
    if name == mise.PROVIDER:
        return mise.Mise(state_root / "providers" / mise.PROVIDER)
    raise ProviderError(f"unknown execution provider: {name}")
