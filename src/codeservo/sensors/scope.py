"""The structural invariants a candidate diff must hold.

Scope is measured from the isolated checkout against the frozen base commit:
which files moved, how far, and whether any of them is a path the constitution
protects.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

from ..domain.constitution import ScopePolicy
from ..domain.results import SensorResult
from ..workspace.git import git


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
