"""The trees a run works on, and the environment they are measured through.

The source repository is the operator's and is only ever read. The candidate
is an isolated shallow checkout with no remote, and the only tree the
controller prepares.
"""

from .git import (
    GitError,
    common_git_dir,
    create_worktree,
    git,
    head,
    is_clean,
    make_patch,
    root,
)

__all__ = [
    "GitError",
    "common_git_dir",
    "create_worktree",
    "git",
    "head",
    "is_clean",
    "make_patch",
    "root",
]
