"""The execution environment a run measures through, as facts about files.

The declaration is frozen from the base commit, the lockfile is resolved to an
inventory, and the candidate's provider files are digested after installation.
Every later phase compares against those digests: what was frozen must still
be what is being measured.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from ..domain.constitution import ExecutionEnvironment
from ..evidence.digests import sha256_file, write_json
from ..workspace import pixi
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
    return {
        "provider": execution.provider,
        "manifest_path": execution.manifest,
        "manifest_sha256": committed_sha256(repo, base_commit, execution.manifest),
        "lock_path": execution.lock,
        "lock_sha256": committed_sha256(repo, base_commit, execution.lock),
        "environment": execution.environment,
    }


def resolved_environment(
    repo: Path,
    run_dir: Path,
    execution: ExecutionEnvironment,
    tasks: tuple[str, ...],
) -> tuple[ResolvedEnvironment, str]:
    """What the lockfile resolves to, and the tasks the environment declares.

    The inventory is stored under the run record, so the packages a
    measurement ran against stay readable from the evidence alone. The
    directory the provider reports for this tree is returned next to it and
    never recorded: it is the operator's location, not a fact about the run.
    """
    resolved = pixi.freeze(
        manifest=repo / execution.manifest,
        lock_path=execution.lock,
        environment=execution.environment,
        tasks=tasks,
    )
    packages_path = run_dir / PACKAGES_RELATIVE_PATH
    write_json(packages_path, resolved.packages)
    record: ResolvedEnvironment = {
        "provider_version": resolved.version,
        "platform": resolved.platform,
        "declared_tasks": list(resolved.tasks),
        "packages_path": PACKAGES_RELATIVE_PATH,
        "packages_sha256": sha256_file(packages_path),
        "package_count": len(resolved.packages),
    }
    return record, resolved.prefix


def optional_sha256(path: Path) -> str | None:
    """The digest of a file, or null where there is no file."""
    return sha256_file(path) if path.is_file() else None


def candidate_digests(
    worktree: Path, execution: ExecutionEnvironment
) -> CandidateDigests:
    """The three provider files of the candidate, as they are right now.

    A file that is gone digests to null, so a deleted manifest, lockfile or
    configuration reads as a change rather than as an unreadable record.
    """
    manifest = worktree / execution.manifest
    return {
        "manifest_sha256": optional_sha256(manifest),
        "lock_sha256": optional_sha256(worktree / execution.lock),
        "config_sha256": optional_sha256(pixi.config_path(manifest)),
    }


def install_candidate(
    worktree: Path, execution: ExecutionEnvironment
) -> tuple[CandidateEnvironment, str]:
    """Install the declared environment into the isolated checkout.

    The candidate is the only tree the controller prepares. The digests are
    taken after the installation, so they describe the workspace every later
    measurement runs against, and are what each recomputation compares to.
    """
    installation = pixi.install(
        manifest=worktree / execution.manifest, environment=execution.environment
    )
    record: CandidateEnvironment = {
        "prefix_path": installation.prefix_path,
        "command": list(installation.command),
        "exit_code": installation.exit_code,
        "duration_ms": installation.duration_ms,
        **candidate_digests(worktree, execution),
        "unchanged_at_end": True,
    }
    return record, installation.diagnostic


def changed_environment(
    environment: EnvironmentBlock,
    worktree: Path,
    execution: ExecutionEnvironment | None,
) -> list[str]:
    """Provider files of the candidate that moved since it was prepared.

    Every measurement runs under variables forbidding it to resolve or
    install, so a manifest, lockfile or provider configuration that differs
    from what was prepared is a control failure of the run and not a failing
    gate: what was frozen is no longer what was measured.
    """
    candidate = environment.get("candidate")
    if execution is None or candidate is None:
        return []
    named = {
        "manifest_sha256": execution.manifest,
        "lock_sha256": execution.lock,
        "config_sha256": pixi.config_path(Path(execution.manifest)).as_posix(),
    }
    prepared: dict[str, str | None] = {
        "manifest_sha256": candidate["manifest_sha256"],
        "lock_sha256": candidate["lock_sha256"],
        "config_sha256": candidate["config_sha256"],
    }
    current = candidate_digests(worktree, execution)
    reasons = [
        f"execution environment: {named[field]} changed during the run"
        for field, digest in current.items()
        if digest != prepared[field]
    ]
    candidate["unchanged_at_end"] = not reasons
    return reasons
