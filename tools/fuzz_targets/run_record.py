"""A record `verify-run` distrusts, moved wherever the fuzzer chooses.

`verify-run` reads a run directory a third party hands it and answers `VALID`,
`INVALID` or `INCOMPLETE`, or refuses the directory outright. Nothing else is
an answer: an auditor who gets a traceback learns nothing about the run, and
the record is exactly the input someone with a reason to falsify it controls.

Random bytes never reach that far — they stop at the JSON decoding, and the
verdict below it would go unmeasured. So the fuzzer does not write the record;
it moves a genuine one. Each input picks a node by descending the document,
then drops it or replaces it with a value of another shape, one to three
times. What is searched is the verification's own branching, with the coverage
feedback steering towards the checks a hand-written case never reaches.
"""

import copy
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import atheris

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src")]

with atheris.instrument_imports():
    from codeservo.evidence.verify import Verdict, VerificationError, verify_run

# The fixture builds the directory; it is not what is measured, so it stays
# outside the instrumentation.
sys.path.insert(0, str(ROOT / "tests"))
from run_fixtures import build_run  # noqa: E402

RUN_DIR = build_run(Path(tempfile.mkdtemp(prefix="codeservo-fuzz-record-")))
RECORD = RUN_DIR / "evidence.json"
GENUINE = json.loads(RECORD.read_text(encoding="utf-8"))

# How many nodes one input may move. More than a few and every record is
# rubble, which the JSON decoding would have refused anyway.
MOVES = 3


def _value(provider: atheris.FuzzedDataProvider) -> Any:
    """One value of a shape the record does not declare at the chosen node."""
    choice = provider.ConsumeIntInRange(0, 7)
    if choice == 0:
        return None
    if choice == 1:
        return provider.ConsumeBool()
    if choice == 2:
        return provider.ConsumeInt(8)
    if choice == 3:
        return provider.ConsumeFloat()
    if choice == 4:
        return provider.ConsumeUnicodeNoSurrogates(64)
    if choice == 5:
        return []
    if choice == 6:
        return {}
    return {provider.ConsumeUnicodeNoSurrogates(8): provider.ConsumeInt(4)}


def _descend(record: dict, provider: atheris.FuzzedDataProvider) -> tuple[Any, Any]:
    """The container and key of one node, chosen by walking down the document.

    An exhausted provider answers zero and false, so the walk stops of its own
    accord rather than depending on how much data the input carried.
    """
    holder: Any = record
    key: Any = None
    node: Any = record
    while isinstance(node, dict | list) and node:
        holder = node
        if isinstance(node, dict):
            names = sorted(node)
            key = names[provider.ConsumeIntInRange(0, len(names) - 1)]
        else:
            key = provider.ConsumeIntInRange(0, len(node) - 1)
        if not provider.ConsumeBool():
            break
        node = node[key]
    return holder, key


def verify_one(data: bytes) -> None:
    provider = atheris.FuzzedDataProvider(data)
    record = copy.deepcopy(GENUINE)
    for _ in range(provider.ConsumeIntInRange(1, MOVES)):
        holder, key = _descend(record, provider)
        if key is None:
            break
        if provider.ConsumeBool():
            del holder[key]
        else:
            holder[key] = _value(provider)

    RECORD.write_text(json.dumps(record), encoding="utf-8")
    try:
        report = verify_run(RUN_DIR)
    except VerificationError:
        return
    if report["status"] not in set(Verdict):
        raise AssertionError(f"the report reached {report['status']!r}")


atheris.Setup(sys.argv, verify_one)
atheris.Fuzz()
