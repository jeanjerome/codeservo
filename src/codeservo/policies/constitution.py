from __future__ import annotations

import re
import tomllib
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from ..domain.constitution import (
    Constitution,
    Direction,
    ExecutionEnvironment,
    Gate,
    Phase,
    Ratchet,
    ResultFormat,
    ReviewPolicy,
    ScopePolicy,
)
from ..workspace.provider import lockfile_of, provider_names

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


def _table(value: Any, what: str) -> dict:
    """One declared table, an absent one being an empty one.

    A constitution is a control input, so every shape it does not have is
    refused by name here. Letting a wrong type reach the reader below would
    end the run on an interpreter traceback, in a place where no decision was
    recorded and nothing says which file was wrong.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConstitutionError(f"{what} must be a table")
    return value


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise ConstitutionError(f"{what} must be a string")
    return value


def _string(item: dict, key: str, what: str) -> str:
    if key not in item:
        raise ConstitutionError(f"{what}: {key} is required")
    return _text(item[key], f"{what}: {key}")


def _integer(item: dict, key: str, default: int, what: str) -> int:
    """One declared integer. TOML booleans are not integers here."""
    value = item.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConstitutionError(f"{what}: {key} must be an integer")
    return value


def _boolean(item: dict, key: str, default: bool, what: str) -> bool:
    value = item.get(key, default)
    if not isinstance(value, bool):
        raise ConstitutionError(f"{what}: {key} must be true or false")
    return value


def _strings(
    item: dict, key: str, default: tuple[str, ...], what: str
) -> tuple[str, ...]:
    value = item.get(key, list(default))
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ConstitutionError(f"{what}: {key} must be an array of strings")
    return tuple(value)


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
    provider = data.get("provider")
    if not isinstance(provider, str) or provider not in provider_names():
        known = ", ".join(provider_names())
        raise ConstitutionError(
            f"execution: provider must be one of {known}, not {provider!r}"
        )

    declared = _string(data, "manifest", "execution").strip()
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
    lock = manifest.parent / lockfile_of(provider)
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
            _text(data.get("environment", "default"), "execution: environment"),
            "execution environment name",
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
        _name(_string(item, "task", f"gate {name}"), f"task name for gate {name}")


def _sensor(item: dict, name: str) -> str | None:
    """The external sensor a gate measures, or nothing when it measures the tree.

    A gate outside the baseline exists to run a sensor frozen before the
    actuation, and a baseline gate measures what the repository already
    carries, so each of the two states excludes the other.
    """
    baseline = _boolean(item, "baseline", True, f"gate {name}")
    sensor = _string(item, "sensor", f"gate {name}") if "sensor" in item else None
    if sensor is not None and not sensor.strip():
        raise ConstitutionError(f"gate {name}: sensor reference cannot be empty")
    if not baseline and sensor is None:
        raise ConstitutionError(
            f"gate {name}: baseline=false requires an external sensor"
        )
    if baseline and sensor is not None:
        raise ConstitutionError(f"gate {name}: external sensor requires baseline=false")
    return sensor


def _ratchets(
    item: dict, name: str, result_format: ResultFormat, baseline: bool
) -> tuple[Ratchet, ...]:
    """The metrics a gate holds between the baseline and the candidate.

    A ratchet compares two documents of one gate, so it is refused on a gate
    that writes none, and on a gate outside the baseline, which measures the
    candidate alone. Either declaration would be a control that can never
    speak, kept in a file that reads as though it did.
    """
    if "ratchet" not in item:
        return ()
    what = f"gate {name}: ratchet"
    table = _table(item["ratchet"], what)
    if not result_format.writes_document:
        raise ConstitutionError(
            f"{what} requires a result_format that writes a document, one of"
            f" {_formats(lambda f: f.writes_document)}"
        )
    if not baseline:
        raise ConstitutionError(
            f"{what} requires a baseline measurement, which baseline=false has none of"
        )
    if not table:
        raise ConstitutionError(f"{what} names no metric")
    ratchets: list[Ratchet] = []
    for metric, value in table.items():
        if not metric:
            raise ConstitutionError(f"{what} names an empty metric")
        ratchets.append(
            Ratchet(
                metric=metric,
                direction=_declared(
                    Direction, _text(value, f"{what} {metric}"), f"{what} {metric}"
                ),
            )
        )
    return tuple(ratchets)


def _formats(wanted: Callable[[ResultFormat], bool]) -> str:
    """The result formats one rule applies to, named as a declaration spells them."""
    return ", ".join(f'"{declared}"' for declared in ResultFormat if wanted(declared))


def _reports(item: dict, name: str, result_format: ResultFormat) -> str | None:
    """Where a gate's own tool writes its reports, and nothing otherwise.

    The pattern is read against the tree the gate measures, the source
    repository during the baseline and the checkout afterwards, so it is
    relative and stays under that tree. A gate whose document the controller
    does not project has no reports to name, and one whose document it
    projects from reports it cannot find would measure nothing.
    """
    what = f"gate {name}: reports"
    if "reports" not in item:
        if result_format.reads_reports:
            raise ConstitutionError(
                f'gate {name}: result_format = "{result_format}" requires'
                " reports, the pattern of the files its tool writes"
            )
        return None
    if not result_format.reads_reports:
        raise ConstitutionError(
            f"{what} requires a result_format the controller projects, one of"
            f" {_formats(lambda f: f.reads_reports)}"
        )
    pattern = _text(item["reports"], what).strip()
    if not pattern:
        raise ConstitutionError(f"{what} names no file")
    if pattern.startswith("/") or ".." in PurePosixPath(pattern).parts:
        raise ConstitutionError(
            f"{what} must stay under the tree the gate measures: {pattern}"
        )
    return pattern


def _gate(item: Any, execution: ExecutionEnvironment | None) -> Gate:
    """One declared gate, held to the shape a gate must have."""
    item = _table(item, "each [[gate]]")
    name = _name(_string(item, "name", "gate"), "gate name")
    _measurement(item, name, execution)
    baseline = _boolean(item, "baseline", True, f"gate {name}")
    result_format = _declared(
        ResultFormat,
        _text(
            item.get("result_format", ResultFormat.EXIT_CODE),
            f"gate {name}: result_format",
        ),
        f"gate {name}: result_format",
    )
    return Gate(
        name=name,
        phase=_declared(
            Phase, _string(item, "phase", f"gate {name}"), f"gate {name}: phase"
        ),
        command=_string(item, "command", f"gate {name}") if "command" in item else None,
        task=_string(item, "task", f"gate {name}") if "task" in item else None,
        timeout_seconds=_integer(item, "timeout_seconds", 300, f"gate {name}"),
        baseline=baseline,
        sensor=_sensor(item, name),
        result_format=result_format,
        reports=_reports(item, name, result_format),
        ratchets=_ratchets(item, name, result_format, baseline),
    )


def load_constitution(repo: Path) -> Constitution:
    path = repo / ".codeservo" / "constitution.toml"
    if not path.is_file():
        raise ConstitutionError(f"missing constitution: {path}")

    # A constitution is a file, so what arrives is bytes: an editor that saved
    # in another encoding produces a control input no decoder accepts, and
    # letting that raise here would end the run before anything was recorded.
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConstitutionError(
            f"constitution is not readable as text: {exc}"
        ) from None
    try:
        document = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise ConstitutionError(
            f"constitution is not readable as TOML: {exc}"
        ) from None
    data = _table(document, "the constitution")

    scope_data = _table(data.get("scope"), "[scope]")
    scope = ScopePolicy(
        protected=_strings(scope_data, "protected", (".codeservo/**",), "[scope]"),
        max_changed_files=_integer(scope_data, "max_changed_files", 30, "[scope]"),
        max_diff_lines=_integer(scope_data, "max_diff_lines", 1000, "[scope]"),
    )

    execution_data = data.get("execution")
    execution = (
        _execution(repo, _table(execution_data, "[execution]"))
        if execution_data is not None
        else None
    )

    gate_items = data.get("gate", [])
    if not isinstance(gate_items, list):
        raise ConstitutionError("[[gate]] must be an array of tables")
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

    review_data = _table(data.get("review"), "[review]")
    review = ReviewPolicy(
        blocking_severities=_strings(
            review_data, "blocking_severities", ("blocker", "major"), "[review]"
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
