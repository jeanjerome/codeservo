"""The execution environment a run measures through, as facts about files.

The declaration is frozen from the base commit, the lockfile is resolved to an
inventory, and the candidate's provider files are digested after installation.
Every later phase compares against those digests: what was frozen must still
be what is being measured.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import fields, replace
from pathlib import Path

from ..domain.constitution import ExecutionEnvironment
from ..domain.document import Unset
from ..evidence.digests import sha256_file, write_json
from ..workspace.provider import Provider
from .document import (
    CandidateDigests,
    CandidateEnvironment,
    EnvironmentBlock,
    ResolvedEnvironment,
)
from .errors import ControlFailure

# Where the inventory the lockfile resolves to is kept, under the run record.
PACKAGES_RELATIVE_PATH = "environment/packages.json"


def committed_sha256(repo: Path, commit: str, relative: str) -> str:
    """The digest of one file as the base commit holds it.

    The frozen control input is the source repository at that commit, not a
    working tree a later step could still touch.
    """
    completed = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "blob", f"{commit}:{relative}"],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ControlFailure(
            f"execution environment: {relative} is not committed at {commit}"
        )
    return hashlib.sha256(completed.stdout).hexdigest()


def frozen_environment(
    repo: Path, base_commit: str, execution: ExecutionEnvironment
) -> EnvironmentBlock:
    """The declaration and the two digests, before any provider command runs."""
    return EnvironmentBlock(
        provider=execution.provider,
        manifest_path=execution.manifest,
        manifest_sha256=committed_sha256(repo, base_commit, execution.manifest),
        lock_path=execution.lock,
        lock_sha256=committed_sha256(repo, base_commit, execution.lock),
        environment=execution.environment,
    )


def resolved_environment(
    repo: Path,
    run_dir: Path,
    execution: ExecutionEnvironment,
    tasks: tuple[str, ...],
    provider: Provider,
) -> tuple[ResolvedEnvironment, str]:
    """What the lockfile resolves to, and the tasks the environment declares.

    The inventory is stored under the run record, so the packages a
    measurement ran against stay readable from the evidence alone. The
    directory the provider reports for this tree is returned next to it and
    never recorded: it is the operator's location, not a fact about the run.
    """
    resolved = provider.freeze(
        manifest=repo / execution.manifest,
        lock_path=execution.lock,
        environment=execution.environment,
        tasks=tasks,
    )
    packages_path = run_dir / PACKAGES_RELATIVE_PATH
    write_json(packages_path, resolved.packages)
    record = ResolvedEnvironment(
        provider_version=resolved.version,
        platform=resolved.platform,
        declared_tasks=tuple(resolved.tasks),
        packages_path=PACKAGES_RELATIVE_PATH,
        packages_sha256=sha256_file(packages_path),
        package_count=len(resolved.packages),
    )
    return record, resolved.prefix


def optional_sha256(path: Path) -> str | None:
    """The digest of a file, or null where there is no file."""
    return sha256_file(path) if path.is_file() else None


def candidate_digests(
    worktree: Path, execution: ExecutionEnvironment, provider: Provider
) -> CandidateDigests:
    """The three provider files of the candidate, as they are right now.

    A file that is gone digests to null, so a deleted manifest, lockfile or
    configuration reads as a change rather than as an unreadable record.
    """
    manifest = worktree / execution.manifest
    return CandidateDigests(
        manifest_sha256=optional_sha256(manifest),
        lock_sha256=optional_sha256(worktree / execution.lock),
        config_sha256=optional_sha256(provider.config_path(manifest)),
    )


def install_candidate(
    worktree: Path, execution: ExecutionEnvironment, provider: Provider
) -> tuple[CandidateEnvironment, str]:
    """Install the declared environment, and digest the tree's provider files.

    A provider installing into the tree is handed the candidate, the only tree
    the controller prepares. One keeping its tools in the controller's own
    directory is handed the source tree, whose files the candidate then
    carries unchanged. The digests are taken after the installation, so they
    describe what every later measurement runs against, and are what each
    recomputation compares to. Whether the workspace held is left unset:
    nothing has been compared yet, and the verdict is what the first
    recomputation establishes.
    """
    installation = provider.install(
        manifest=worktree / execution.manifest, environment=execution.environment
    )
    digests = candidate_digests(worktree, execution, provider)
    record = CandidateEnvironment(
        prefix_path=installation.prefix_path,
        command=tuple(installation.command),
        exit_code=installation.exit_code,
        duration_ms=installation.duration_ms,
        manifest_sha256=digests.manifest_sha256,
        lock_sha256=digests.lock_sha256,
        config_sha256=digests.config_sha256,
    )
    return record, installation.diagnostic


def changed_environment(
    environment: EnvironmentBlock,
    worktree: Path,
    execution: ExecutionEnvironment | None,
    provider: Provider | None,
) -> tuple[EnvironmentBlock, list[str]]:
    """Provider files of the candidate that moved since it was prepared.

    Every measurement runs under variables forbidding it to resolve or
    install, so a manifest, lockfile or provider configuration that differs
    from what was prepared is a control failure of the run and not a failing
    gate: what was frozen is no longer what was measured.

    The block comes back stating what this reading found, because whether the
    candidate's environment held is a measurement and the record carries it.
    """
    candidate = environment.candidate
    if execution is None or provider is None or isinstance(candidate, Unset):
        return environment, []
    named = {
        "manifest_sha256": execution.manifest,
        "lock_sha256": execution.lock,
        "config_sha256": provider.config_path(Path(execution.manifest)).as_posix(),
    }
    current = candidate_digests(worktree, execution, provider)
    reasons = [
        f"execution environment: {named[declared.name]} changed during the run"
        for declared in fields(CandidateDigests)
        if getattr(current, declared.name) != getattr(candidate, declared.name)
    ]
    return (
        replace(
            environment,
            candidate=replace(candidate, unchanged_at_end=not reasons),
        ),
        reasons,
    )
