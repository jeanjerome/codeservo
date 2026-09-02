"""Copying the external sensors into the record, and watching them after.

A sensor is a control input the actuator must never reach. It is copied into
the run directory before the candidate exists, digested there, and digested
again after each measurement phase: a gate that wrote into a frozen sensor
changed what a later reading reports.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..domain.constitution import Constitution
from ..evidence.digests import sha256_path
from .errors import ControlFailure

IGNORED_WHEN_COPYING = ("__pycache__", ".pytest_cache", "*.pyc", ".DS_Store")


def freeze_sensors(
    state_root: Path, run_dir: Path, constitution: Constitution
) -> tuple[dict[str, Path], dict[str, dict]]:
    sensor_root = (state_root / "sensors").resolve()
    paths: dict[str, Path] = {}
    evidence: dict[str, dict] = {}
    for gate in constitution.gates:
        if gate.sensor is None:
            continue
        reference = Path(gate.sensor)
        unresolved_source = sensor_root / reference
        source = unresolved_source.resolve()
        if (
            reference == Path(".")
            or reference.is_absolute()
            or not source.is_relative_to(sensor_root)
        ):
            raise ControlFailure(
                f"gate {gate.name}: sensor must stay under {sensor_root}"
            )
        if not source.exists():
            raise ControlFailure(f"gate {gate.name}: missing external sensor {source}")
        lexical_sources = (unresolved_source, *unresolved_source.parents)
        if any(
            path.is_relative_to(sensor_root) and path.is_symlink()
            for path in lexical_sources
        ) or any(path.is_symlink() for path in source.rglob("*")):
            raise ControlFailure(
                f"gate {gate.name}: sensor cannot contain symbolic links"
            )

        target = run_dir / "sensors" / gate.name
        if source.is_dir():
            shutil.copytree(
                source,
                target,
                ignore=shutil.ignore_patterns(*IGNORED_WHEN_COPYING),
            )
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        paths[gate.name] = target
        evidence[gate.name] = {
            "path": str(target),
            "reference": gate.sensor,
            "sha256": sha256_path(target),
        }
    return paths, evidence


def altered_sensors(
    sensor_paths: dict[str, Path], sensor_evidence: dict[str, dict]
) -> list[str]:
    """Frozen sensors whose content changed after the controller froze them."""
    return sorted(
        name
        for name, path in sensor_paths.items()
        if sha256_path(path) != sensor_evidence[name]["sha256"]
    )


def sensor_tampering(
    sensor_paths: dict[str, Path], sensor_evidence: dict[str, dict]
) -> list[str]:
    """The control failures an altered sensor amounts to."""
    return [
        f"gate altered the frozen sensor {name}"
        for name in altered_sensors(sensor_paths, sensor_evidence)
    ]
