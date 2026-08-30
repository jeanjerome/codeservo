from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

from .model import ScopePolicy, SensorResult


class GitError(RuntimeError):
    pass


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=False
    )
    if check and result.returncode != 0:
        raise GitError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def root(path: Path) -> Path:
    return Path(git(path, "rev-parse", "--show-toplevel").strip()).resolve()


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def is_clean(repo: Path) -> bool:
    return not git(repo, "status", "--porcelain").strip()


def common_git_dir(repo: Path) -> Path:
    path = Path(git(repo, "rev-parse", "--git-common-dir").strip())
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def create_worktree(repo: Path, destination: Path, commit: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    git(
        repo,
        "clone",
        "--no-local",
        "--depth",
        "1",
        "--single-branch",
        repo.as_uri(),
        str(destination),
    )
    if head(destination) != commit:
        raise GitError("isolated checkout does not match the frozen base commit")
    git(destination, "remote", "remove", "origin")


def changed_files(worktree: Path, base_commit: str) -> list[str]:
    tracked = [
        x
        for x in git(worktree, "diff", "--name-only", base_commit, "--").splitlines()
        if x
    ]
    untracked = [
        x
        for x in git(worktree, "ls-files", "--others", "--exclude-standard").splitlines()
        if x
    ]
    return sorted(set(tracked + untracked))


def diff_line_count(worktree: Path, base_commit: str) -> int:
    total = 0
    numstat = git(worktree, "diff", "--numstat", base_commit, "--")
    for line in numstat.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 2:
            continue
        for value in parts[:2]:
            if value.isdigit():
                total += int(value)
    for name in git(worktree, "ls-files", "--others", "--exclude-standard").splitlines():
        path = worktree / name
        if path.is_file():
            try:
                total += len(path.read_text(encoding="utf-8").splitlines())
            except UnicodeDecodeError:
                pass
    return total


def scope_sensor(worktree: Path, base_commit: str, policy: ScopePolicy) -> SensorResult:
    files = changed_files(worktree, base_commit)
    violations: list[str] = []
    for path in files:
        for pattern in policy.protected:
            normalized = pattern[:-3] + "*" if pattern.endswith("/**") else pattern
            if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, normalized):
                violations.append(f"protected path changed: {path} (pattern {pattern})")
                break

    line_count = diff_line_count(worktree, base_commit)
    if len(files) > policy.max_changed_files:
        violations.append(
            f"changed files {len(files)} > max_changed_files {policy.max_changed_files}"
        )
    if line_count > policy.max_diff_lines:
        violations.append(f"diff lines {line_count} > max_diff_lines {policy.max_diff_lines}")

    return SensorResult(
        name="scope",
        passed=not violations,
        summary="scope OK" if not violations else "; ".join(violations),
        details={"changed_files": files, "diff_lines": line_count, "violations": violations},
    )


def make_patch(worktree: Path, base_commit: str) -> str:
    parts = [git(worktree, "diff", "--binary", "--no-ext-diff", base_commit, "--", check=False)]
    untracked = git(worktree, "ls-files", "--others", "--exclude-standard").splitlines()
    for rel in untracked:
        p = worktree / rel
        if not p.is_file():
            continue
        result = subprocess.run(
            ["git", "diff", "--no-index", "--binary", "--", "/dev/null", rel],
            cwd=worktree,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.stdout:
            parts.append(result.stdout)
    return "\n".join(parts)
