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
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path
from typing import Any, TypedDict

from ..resources import observation_schema

# The variable naming the file to write. It reaches the process of the gate
# that declared the format, and no other.
OBSERVATION_PATH_VARIABLE = "CODESERVO_OBSERVATION_PATH"

# The shape of the document. The observation versions its own shape.
SCHEMA_VERSION = 1


class Status(StrEnum):
    """What a document says about the gate that wrote it."""

    PASSED = "passed"
    FAILED = "failed"


class Severity(StrEnum):
    """How severe a finding a gate may raise.

    This is what an observation carries; the reviewer answers a different
    schema, and the severities it may raise are that schema's own.
    """

    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"
    INFO = "info"


class Finding(TypedDict):
    """One thing a sensor saw, and where it saw it."""

    id: str
    severity: Severity
    path: str
    line: int
    message: str


class Observation(TypedDict):
    """The document a `codeservo-json` gate writes beside its exit code.

    The six fields are the whole contract. They are declared once here; the
    field sets the validation enforces and the published schema declares are
    both read from this shape.
    """

    schema_version: int
    sensor: str
    status: Status
    summary: str
    findings: list[Finding]
    metrics: dict[str, float]


OBSERVATION_FIELDS = frozenset(Observation.__annotations__)
FINDING_FIELDS = frozenset(Finding.__annotations__)


class Classification(StrEnum):
    """How a gate's document ended up in the record."""

    VALID = "valid"
    ABSENT = "absent"
    INVALID = "invalid"
    CONTRADICTED = "contradicted"


class ObservationPathError(RuntimeError):
    """The controller cannot own the location it would hand to a gate."""


def schema_path(source_root: Path | None = None) -> Path:
    """Locate the published observation schema."""
    return observation_schema(source_root)


def is_json_number(value: Any) -> bool:
    """Whether a parsed value is a JSON number.

    Python's `bool` is an `int` and `True == 1`, so a boolean would otherwise
    pass for a number everywhere a number is expected. It is refused here, once,
    for every field that wants one.
    """
    return isinstance(value, int | float) and not isinstance(value, bool)


def is_json_integer(value: Any) -> bool:
    """Whether a parsed value is a JSON integer, a boolean never being one."""
    return isinstance(value, int) and not isinstance(value, bool)


def _refuse_constant(name: str) -> Any:
    """Refuse the three constants Python accepts and JSON does not define."""
    raise ValueError(f"{name} is not a JSON value")


def _listed(values: Iterable[str]) -> str:
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
    if finding["severity"] not in Severity:
        return f"field {where}.severity must be one of {_listed(Severity)}"
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
    if document["status"] not in Status:
        return f"field status must be one of {_listed(Status)}"
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


def validate(raw: bytes) -> tuple[Observation | None, str | None]:
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


def classify(
    raw: bytes | None, *, passed: bool
) -> tuple[Classification, str | None]:
    """How one gate's document stands against the exit code that is the verdict.

    A document the gate never wrote is `absent`, one that breaks the contract is
    `invalid`, and one that disagrees with the exit code is `contradicted`.
    Nothing the document says changes whether the gate passed.
    """
    if raw is None:
        return Classification.ABSENT, "the gate wrote no observation"
    document, wrong = validate(raw)
    if document is None:
        return Classification.INVALID, wrong
    verdict = Status.PASSED if passed else Status.FAILED
    if document["status"] != verdict:
        return (
            Classification.CONTRADICTED,
            f"the observation reports {document['status']} for a gate that"
            f" {'passed' if passed else 'did not pass'}",
        )
    return Classification.VALID, None
