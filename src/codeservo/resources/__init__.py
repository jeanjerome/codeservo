"""The documents this package publishes, and where a reader finds them.

A published document exists twice: once under the repository's `templates/`,
which is what a target repository reads and what the documentation points at,
and once inside the package, because an installed wheel carries no
repository-level `templates/`. The repository copy wins when it is there, so
a source checkout reads the document it also publishes.
"""

from __future__ import annotations

from pathlib import Path

# Where this file sits inside a source checkout, and the checkout it sits in.
PACKAGE_DIR = Path(__file__).resolve().parent
SOURCE_ROOT = PACKAGE_DIR.parents[2]

PUBLISHED_DIRECTORY = "templates"


def published(name: str, source_root: Path | None = None) -> Path:
    """The repository copy of a published document, or the packaged one."""
    root = source_root if source_root is not None else SOURCE_ROOT
    repository_copy = root / PUBLISHED_DIRECTORY / name
    return repository_copy if repository_copy.is_file() else PACKAGE_DIR / name


def observation_schema(source_root: Path | None = None) -> Path:
    """The schema of the document a `codeservo-json` gate writes."""
    return published("observation.schema.json", source_root)


def review_schema(source_root: Path | None = None) -> Path:
    """The schema the read-only reviewer answers against."""
    return published("review.schema.json", source_root)


def constitution_example() -> Path:
    """The starter constitution `codeservo init` copies into a repository."""
    return PACKAGE_DIR / "constitution.example.toml"
