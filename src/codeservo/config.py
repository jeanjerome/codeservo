from __future__ import annotations

import tomllib
from pathlib import Path

from .model import Constitution, Gate, ReviewPolicy, ScopePolicy


class ConstitutionError(ValueError):
    pass


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

    gate_items = data.get("gate", [])
    if not gate_items:
        raise ConstitutionError("constitution must declare at least one [[gate]]")

    gates: list[Gate] = []
    names: set[str] = set()
    for item in gate_items:
        name = str(item["name"])
        if name in names:
            raise ConstitutionError(f"duplicate gate name: {name}")
        names.add(name)
        phase = str(item["phase"])
        if phase not in {"quick", "full"}:
            raise ConstitutionError(f"gate {name}: phase must be quick or full")
        gates.append(
            Gate(
                name=name,
                phase=phase,  # type: ignore[arg-type]
                command=str(item["command"]),
                timeout_seconds=int(item.get("timeout_seconds", 300)),
                baseline=bool(item.get("baseline", True)),
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

    return Constitution(path=path, raw_text=raw, scope=scope, gates=tuple(gates), review=review)
