from __future__ import annotations

import subprocess
from pathlib import Path


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
