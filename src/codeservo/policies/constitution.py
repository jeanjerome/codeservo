from __future__ import annotations

import re
import tomllib
from enum import StrEnum
from pathlib import Path

from ..domain.constitution import (
    Constitution,
    ExecutionEnvironment,
    Gate,
    Phase,
    ResultFormat,
    ReviewPolicy,
    ScopePolicy,
)
from ..workspace import pixi

NAME_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]*"


class ConstitutionError(ValueError):
    pass


def _declared[Member: StrEnum](
    vocabulary: type[Member], value: str, what: str
) -> Member:
    """One member of a declared vocabulary, or a refusal naming what was asked.

    The member returned is the one the domain declares, never the string that
    was read, so a value that reached this point is the vocabulary and not
    something that merely compares equal to it.
    """
    try:
        return vocabulary(value)
    except ValueError:
        known = ", ".join(vocabulary)
        raise ConstitutionError(
            f"{what} must be one of {known}, not {value!r}"
        ) from None


def _name(value: str, what: str) -> str:
    if re.fullmatch(NAME_PATTERN, value) is None:
        raise ConstitutionError(f"invalid {what}: {value}")
    return value


def _execution(repo: Path, data: dict) -> ExecutionEnvironment:
    """Resolve the declared execution environment against the repository.

    The manifest and its lockfile are the frozen control input of a run, so a
    provider declared without both of them freezes nothing and is refused
    here rather than discovered when a gate runs.
    """
    provider = str(data.get("provider", ""))
    if provider != pixi.PROVIDER:
        raise ConstitutionError(
            f"execution: provider must be {pixi.PROVIDER}, not {provider!r}"
        )

    declared = str(data.get("manifest", "")).strip()
    if not declared:
        raise ConstitutionError("execution: manifest is required")
    root = repo.resolve()
    manifest = (root / declared).resolve()
    if (
        Path(declared).is_absolute()
        or not manifest.is_relative_to(root)
        or manifest == root
    ):
        raise ConstitutionError(
            f"execution: manifest must stay under the repository root: {declared}"
        )
    if not manifest.is_file():
        raise ConstitutionError(f"execution: missing manifest {declared}")
    lock = manifest.parent / pixi.LOCKFILE
    if not lock.is_file():
        raise ConstitutionError(
            f"execution: provider {provider} requires"
            f" {lock.relative_to(root).as_posix()}"
        )

    return ExecutionEnvironment(
        provider=provider,
        manifest=manifest.relative_to(root).as_posix(),
        lock=lock.relative_to(root).as_posix(),
        environment=_name(
            str(data.get("environment", "default")), "execution environment name"
        ),
    )


def _measurement(item: dict, name: str, execution: ExecutionEnvironment | None) -> None:
    """What a gate names as the measurement it runs.

    A gate names a shell command or a provider task and never both, and a task
    means nothing without a provider to project it onto a command line.
    """
    command = "command" in item
    task = "task" in item
    if command and task:
        raise ConstitutionError(f"gate {name}: declares both a command and a task")
    if not command and not task:
        raise ConstitutionError(f"gate {name}: declares neither command nor task")
    if task:
        if execution is None:
            raise ConstitutionError(
                f"gate {name}: task requires an [execution] provider"
            )
        _name(str(item["task"]), f"task name for gate {name}")


def _sensor(item: dict, name: str) -> str | None:
    """The external sensor a gate measures, or nothing when it measures the tree.

    A gate outside the baseline exists to run a sensor frozen before the
    actuation, and a baseline gate measures what the repository already
    carries, so each of the two states excludes the other.
    """
    baseline = bool(item.get("baseline", True))
    sensor = str(item["sensor"]) if "sensor" in item else None
    if sensor is not None and not sensor.strip():
        raise ConstitutionError(f"gate {name}: sensor reference cannot be empty")
    if not baseline and sensor is None:
        raise ConstitutionError(
            f"gate {name}: baseline=false requires an external sensor"
        )
    if baseline and sensor is not None:
        raise ConstitutionError(
            f"gate {name}: external sensor requires baseline=false"
        )
    return sensor


def _gate(item: dict, execution: ExecutionEnvironment | None) -> Gate:
    """One declared gate, held to the shape a gate must have."""
    name = _name(str(item["name"]), "gate name")
    _measurement(item, name, execution)
    return Gate(
        name=name,
        phase=_declared(Phase, str(item["phase"]), f"gate {name}: phase"),
        command=str(item["command"]) if "command" in item else None,
        task=str(item["task"]) if "task" in item else None,
        timeout_seconds=int(item.get("timeout_seconds", 300)),
        baseline=bool(item.get("baseline", True)),
        sensor=_sensor(item, name),
        result_format=_declared(
            ResultFormat,
            str(item.get("result_format", ResultFormat.EXIT_CODE)),
            f"gate {name}: result_format",
        ),
    )


def load_constitution(repo: Path) -> Constitution:
    path = repo / ".codeservo" / "constitution.toml"
    if not path.is_file():
        raise ConstitutionError(f"missing constitution: {path}")

    raw = path.read_text(encoding="utf-8")
    data = tomllib.loads(raw)

    scope_data = data.get("scope", {})
    scope = ScopePolicy(
        protected=tuple(scope_data.get("protected", [".codeservo/**"])),
        max_changed_files=int(scope_data.get("max_changed_files", 30)),
        max_diff_lines=int(scope_data.get("max_diff_lines", 1000)),
    )

    execution_data = data.get("execution")
    execution = (
        _execution(repo, execution_data) if execution_data is not None else None
    )

    gate_items = data.get("gate", [])
    if not gate_items:
        raise ConstitutionError("constitution must declare at least one [[gate]]")

    gates: list[Gate] = []
    names: set[str] = set()
    for item in gate_items:
        gate = _gate(item, execution)
        if gate.name in names:
            raise ConstitutionError(f"duplicate gate name: {gate.name}")
        names.add(gate.name)
        gates.append(gate)

    phases = {gate.phase for gate in gates}
    for required in Phase:
        if required not in phases:
            raise ConstitutionError(
                f"constitution must declare at least one {required} gate"
            )

    review_data = data.get("review", {})
    review = ReviewPolicy(
        blocking_severities=tuple(
            str(x) for x in review_data.get("blocking_severities", ["blocker", "major"])
        )
    )

    return Constitution(
        path=path,
        raw_text=raw,
        scope=scope,
        gates=tuple(gates),
        review=review,
        execution=execution,
    )
