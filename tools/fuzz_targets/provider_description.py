"""Bytes offered as a provider's self-description, and what they may reach.

`pixi info --json` is read before anything is measured, and it is another
program's standard output: a version that renames a key, a build that prints a
warning ahead of the document, a process killed mid-write. The reader answers
with the four facts a run measures through, or refuses by name.

The document arrives as bytes and is decoded before it is parsed, so what is
fuzzed is the whole path from those bytes to the refusal.
"""

import sys
from pathlib import Path

import atheris

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src")]

with atheris.instrument_imports():
    from codeservo.workspace.pixi import ProviderError, read_description


def read_one(data: bytes) -> None:
    try:
        read_description(
            data.decode("utf-8", "replace"),
            manifest_name="pyproject.toml",
            environment="default",
        )
    except ProviderError:
        return


atheris.Setup(sys.argv, read_one)
atheris.Fuzz()
