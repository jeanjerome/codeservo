from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_record(payload: dict[str, Any]) -> str:
    stable_fields = {
        key: value
        for key, value in payload.items()
        if key != "result_sha256" and not key.endswith("_path")
    }
    return sha256_json(stable_fields)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    files = (
        [path]
        if path.is_file()
        else sorted(item for item in path.rglob("*") if item.is_file())
    )
    for file_path in files:
        relative = (
            file_path.name
            if path.is_file()
            else file_path.relative_to(path).as_posix()
        )
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def relative_evidence_paths(payload: Any, run_dir: Path) -> Any:
    portable = copy.deepcopy(payload)

    def visit(value: Any, key: str | None = None) -> Any:
        if isinstance(value, dict):
            return {item_key: visit(item, item_key) for item_key, item in value.items()}
        if isinstance(value, list):
            return [visit(item, key) for item in value]
        if not isinstance(value, str):
            return value

        path_key = key in {"path", "repo", "run_dir", "state_dir", "worktree"}
        path_key = path_key or bool(key and key.endswith("_path"))
        path_key = path_key or key in {"denied_paths", "read_only_paths"}
        if not path_key:
            return value

        path = Path(value)
        if not path.is_absolute():
            return value
        return Path(os.path.relpath(path, start=run_dir)).as_posix()

    return visit(portable)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
