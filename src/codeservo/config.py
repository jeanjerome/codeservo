from __future__ import annotations

import re
import tomllib
from pathlib import Path

from . import observations, pixi
from .model import (
    Constitution,
    ExecutionEnvironment,
    Gate,
    ReviewPolicy,
    ScopePolicy,
)

NAME_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]*"


class ConstitutionError(ValueError):
    pass


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
        name = _name(str(item["name"]), "gate name")
        if name in names:
            raise ConstitutionError(f"duplicate gate name: {name}")
        names.add(name)
        phase = str(item["phase"])
        if phase not in {"quick", "full"}:
            raise ConstitutionError(f"gate {name}: phase must be quick or full")
        command = str(item["command"]) if "command" in item else None
        task = str(item["task"]) if "task" in item else None
        if command is not None and task is not None:
            raise ConstitutionError(
                f"gate {name}: declares both a command and a task"
            )
        if command is None and task is None:
            raise ConstitutionError(f"gate {name}: declares neither command nor task")
        if task is not None:
            if execution is None:
                raise ConstitutionError(
                    f"gate {name}: task requires an [execution] provider"
                )
            _name(task, f"task name for gate {name}")
        result_format = str(item.get("result_format", observations.EXIT_CODE))
        if result_format not in observations.RESULT_FORMATS:
            raise ConstitutionError(
                f"gate {name}: result_format must be one of"
                f" {', '.join(observations.RESULT_FORMATS)}, not {result_format!r}"
            )
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
        gates.append(
            Gate(
                name=name,
                phase=phase,  # type: ignore[arg-type]
                command=command,
                task=task,
                timeout_seconds=int(item.get("timeout_seconds", 300)),
                baseline=baseline,
                sensor=sensor,
                result_format=result_format,
            )
        )

    phases = {gate.phase for gate in gates}
    if "quick" not in phases:
        raise ConstitutionError("constitution must declare at least one quick gate")
    if "full" not in phases:
        raise ConstitutionError("constitution must declare at least one full gate")

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
