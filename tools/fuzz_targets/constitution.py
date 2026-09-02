"""Bytes offered as a constitution, and the one answer they may reach.

The constitution is the first control input of every run and the only one a
maintainer writes by hand. What arrives here is a file, so it is bytes: an
editor that saved in another encoding, a truncated write, a document that is
valid TOML and says nothing the reader knows. The reader has exactly one
honest answer to all of them, a refusal naming what is wrong, because a
traceback ends the run before any decision was recorded.

Properties state this over generated text, which is always encodable. A fuzzer
states it over bytes, which are not.
"""

import sys
import tempfile
from pathlib import Path

import atheris

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src")]

with atheris.instrument_imports():
    from codeservo.policies.constitution import ConstitutionError, load_constitution

# One repository for the whole session: the boundary under test is the reading
# and not the making of a directory, and a fuzzer runs this thousands of times.
REPO = Path(tempfile.mkdtemp(prefix="codeservo-fuzz-constitution-"))
(REPO / ".codeservo").mkdir()
CONSTITUTION = REPO / ".codeservo" / "constitution.toml"


def read_one(data: bytes) -> None:
    CONSTITUTION.write_bytes(data)
    try:
        load_constitution(REPO)
    except ConstitutionError:
        return


atheris.Setup(sys.argv, read_one)
atheris.Fuzz()
