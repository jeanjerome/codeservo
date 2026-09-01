"""The structured document a gate may return beside its exit code.

A gate answers with an exit code, and that stays the verdict. A gate that
declares `codeservo-json` answers a second time, with a document saying what
it measured, written where the controller told it to write.

This module owns the contract: where the published schema lives, what a valid
document is, and what to say about one that is not. The schema is a document
the package publishes so a target repository can write an adapter against it;
nothing here interprets it. The six fields are known, and they are checked by
name.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# What a gate may declare it answers with. An undeclared gate answers with its
# exit code alone, exactly as every gate did before.
EXIT_CODE = "exit-code"
CODESERVO_JSON = "codeservo-json"
RESULT_FORMATS = (EXIT_CODE, CODESERVO_JSON)

# The variable naming the file to write. It reaches the process of the gate
# that declared the format, and no other.
OBSERVATION_PATH_VARIABLE = "CODESERVO_OBSERVATION_PATH"

# The shape of the document. The observation versions its own shape.
SCHEMA_VERSION = 1

OBSERVATION_FIELDS = frozenset(
    {"schema_version", "sensor", "status", "summary", "findings", "metrics"}
)
FINDING_FIELDS = frozenset({"id", "severity", "path", "line", "message"})
STATUSES = ("passed", "failed")
SEVERITIES = ("blocker", "major", "minor", "info")

# How a gate's document ended up in the record.
VALID = "valid"
ABSENT = "absent"
INVALID = "invalid"
CONTRADICTED = "contradicted"


class ObservationPathError(RuntimeError):
    """The controller cannot own the location it would hand to a gate."""


def schema_path(source_root: Path | None = None) -> Path:
    """Locate the published observation schema.

    An installed wheel carries no repository-level `templates/`, so the package
    keeps its own copy of the schema next to the module.
    """
    root = (
        source_root
        if source_root is not None
        else Path(__file__).resolve().parents[2]
    )
    repository_copy = root / "templates" / "observation.schema.json"
    if repository_copy.is_file():
        return repository_copy
    return Path(__file__).with_name("observation.schema.json")


def is_json_number(value: Any) -> bool:
    """Whether a parsed value is a JSON number.

    Python's `bool` is an `int` and `True == 1`, so a boolean would otherwise
    pass for a number everywhere a number is expected. It is refused here, once,
    for every field that wants one.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_json_integer(value: Any) -> bool:
    """Whether a parsed value is a JSON integer, a boolean never being one."""
    return isinstance(value, int) and not isinstance(value, bool)


def _refuse_constant(name: str) -> Any:
    """Refuse the three constants Python accepts and JSON does not define."""
    raise ValueError(f"{name} is not a JSON value")


def _listed(values: frozenset[str] | tuple[str, ...]) -> str:
    return ", ".join(sorted(values))


def _field_set(
    value: dict, allowed: frozenset[str], where: str, what: str
) -> str | None:
    for key in sorted(value):
        if key not in allowed:
            return f"unknown field {where}{key}: {what} carries exactly {_listed(allowed)}"
    for key in sorted(allowed):
        if key not in value:
            return f"missing field {where}{key}: {what} carries exactly {_listed(allowed)}"
    return None


def _finding(finding: Any, index: int) -> str | None:
    where = f"findings[{index}]"
    if not isinstance(finding, dict):
        return f"field {where} must be an object"
    wrong = _field_set(finding, FINDING_FIELDS, f"{where}.", "a finding")
    if wrong is not None:
        return wrong
    if not isinstance(finding["id"], str) or not finding["id"]:
        return f"field {where}.id must be a non-empty string"
    if finding["severity"] not in SEVERITIES:
        return f"field {where}.severity must be one of {_listed(SEVERITIES)}"
    if finding["path"] is not None and not isinstance(finding["path"], str):
        return f"field {where}.path must be a string or null"
    line = finding["line"]
    if line is not None and not (is_json_integer(line) and line >= 1):
        return f"field {where}.line must be an integer of at least 1, or null"
    if not isinstance(finding["message"], str) or not finding["message"]:
        return f"field {where}.message must be a non-empty string"
    return None


def _contract(document: Any) -> str | None:
    """What the document violates, or nothing when it violates nothing."""
    if not isinstance(document, dict):
        return "the observation must be a JSON object"
    wrong = _field_set(document, OBSERVATION_FIELDS, "", "the observation")
    if wrong is not None:
        return wrong
    version = document["schema_version"]
    if not is_json_integer(version) or version != SCHEMA_VERSION:
        return f"field schema_version must be the integer {SCHEMA_VERSION}"
    if not isinstance(document["sensor"], str) or not document["sensor"]:
        return "field sensor must be a non-empty string"
    if document["status"] not in STATUSES:
        return f"field status must be one of {_listed(STATUSES)}"
    if not isinstance(document["summary"], str):
        return "field summary must be a string"
    findings = document["findings"]
    if not isinstance(findings, list):
        return "field findings must be an array of objects"
    for index, finding in enumerate(findings):
        wrong = _finding(finding, index)
        if wrong is not None:
            return wrong
    metrics = document["metrics"]
    if not isinstance(metrics, dict):
        return "field metrics must be an object"
    for key in sorted(metrics):
        if not is_json_number(metrics[key]):
            return f"field metrics.{key} must be a number"
    return None


def validate(raw: bytes) -> tuple[dict | None, str | None]:
    """Read one document as JSON and hold it to the six fields it must carry.

    Returns the parsed document, or the fault that names the field it violated
    and what was expected of it.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "the observation is not valid UTF-8"
    try:
        document = json.loads(text, parse_constant=_refuse_constant)
    except ValueError as exc:
        return None, f"the observation is not JSON: {exc}"
    wrong = _contract(document)
    if wrong is not None:
        return None, wrong
    return document, None


def classify(raw: bytes | None, *, passed: bool) -> tuple[str, str | None]:
    """How one gate's document stands against the exit code that is the verdict.

    A document the gate never wrote is `absent`, one that breaks the contract is
    `invalid`, and one that disagrees with the exit code is `contradicted`.
    Nothing the document says changes whether the gate passed.
    """
    if raw is None:
        return ABSENT, "the gate wrote no observation"
    document, wrong = validate(raw)
    if wrong is not None:
        return INVALID, wrong
    verdict = STATUSES[0] if passed else STATUSES[1]
    if document["status"] != verdict:
        return (
            CONTRADICTED,
            f"the observation reports {document['status']} for a gate that"
            f" {'passed' if passed else 'did not pass'}",
        )
    return VALID, None
