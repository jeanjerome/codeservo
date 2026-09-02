"""Project the model caches a backend keeps on this machine.

The projection reads files. It starts no agent, refreshes no provider cache and
touches no target repository, so an inventory line reports what a backend
advertises locally. That is not proof that an account, an organization or the
current quota authorizes the model.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypedDict, get_args

SCHEMA_VERSION = 1
Backend = Literal["claude", "codex"]
BACKEND_NAMES: tuple[Backend, ...] = get_args(Backend)

SOURCE_CACHE = "backend-cache"
SOURCE_UNAVAILABLE = "unavailable"
STATUS_ADVERTISED = "advertised"
STATUS_INELIGIBLE = "ineligible"

# The speed tiers a run may request. `standard` is what a backend applies when
# no tier is asked for, so it is also the documented default.
Speed = Literal["standard", "fast"]
SPEED_NAMES: tuple[Speed, ...] = get_args(Speed)
DEFAULT_SPEED: Speed = "standard"

# How the requested profile compares to the local inventory. `unsupported` is
# the only refusal: it needs an inventory that lists the model and contradicts
# the request. Everything the inventory cannot settle stays `unverified`,
# because a cache that does not list a model is not an authority on access.
ProfileStatus = Literal["supported", "unsupported", "unverified"]
PROFILE_SUPPORTED: ProfileStatus = "supported"
PROFILE_UNSUPPORTED: ProfileStatus = "unsupported"
PROFILE_UNVERIFIED: ProfileStatus = "unverified"

# The reason never quotes the provider document, so no value the cache holds
# beyond the projected fields reaches the inventory.
NOT_LISTED_REASON = "the backend cache does not offer this model for selection"
CLAUDE_UNVERIFIED_REASON = "no cache schema has been verified for this backend"


class ProfileVerdict(TypedDict):
    """What the local inventory of one backend can say about a request."""

    status: ProfileStatus
    reason: str
    inventory_source: str


class ModelSelectionError(ValueError):
    """A backend or a model the inventory does not report."""


def _home(env: Mapping[str, str]) -> Path:
    home = env.get("HOME", "").strip()
    return Path(home) if home else Path.home()


def codex_cache_path(env: Mapping[str, str]) -> Path:
    codex_home = env.get("CODEX_HOME", "").strip()
    root = Path(codex_home) if codex_home else _home(env) / ".codex"
    return root / "models_cache.json"


def claude_cache_path(env: Mapping[str, str]) -> Path:
    config_dir = env.get("CLAUDE_CONFIG_DIR", "").strip()
    root = Path(config_dir) if config_dir else _home(env) / ".claude"
    return root / "models_cache.json"


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _unavailable(backend: str, reason: str) -> dict:
    """Report a source the command could not project.

    A projected backend entry carries the documented fields and nothing else, so
    `unavailable_reason` is added only here, where a reason exists to state.
    """
    return {
        "backend": backend,
        "source": SOURCE_UNAVAILABLE,
        "source_observed_at": None,
        "cli_version": None,
        "skipped_entries": 0,
        "models": [],
        "unavailable_reason": reason,
    }


def _efforts(levels: Any) -> list[str] | None:
    """Order the declared reasoning levels, or reject a non-conforming entry."""
    if levels is None:
        return []
    if not isinstance(levels, list):
        return None
    efforts: list[str] = []
    for level in levels:
        if not isinstance(level, dict):
            return None
        effort = level.get("effort")
        if not isinstance(effort, str) or not effort.strip():
            return None
        efforts.append(effort)
    return efforts


def _speeds(tiers: Any) -> list[str] | None:
    if tiers is None:
        return [DEFAULT_SPEED]
    if not isinstance(tiers, list):
        return None
    return list(SPEED_NAMES) if "fast" in tiers else [DEFAULT_SPEED]


def _project_codex_entry(
    entry: Any, observed_at: str | None, cli_version: str | None
) -> dict | None:
    """Project one cache entry, or return None when it does not conform.

    A field carrying the wrong type makes the record unusable, so the entry is
    skipped. A field the record simply omits leaves the projection empty for
    that field, because an absent detail is not a malformed one.
    """
    if not isinstance(entry, dict):
        return None
    slug = entry.get("slug")
    if not isinstance(slug, str) or not slug.strip():
        return None
    display_name = entry.get("display_name")
    if display_name is not None and not isinstance(display_name, str):
        return None
    default_effort = entry.get("default_reasoning_level")
    if default_effort is not None and not isinstance(default_effort, str):
        return None
    visibility = entry.get("visibility", "list")
    if not isinstance(visibility, str):
        return None
    efforts = _efforts(entry.get("supported_reasoning_levels"))
    speeds = _speeds(entry.get("additional_speed_tiers"))
    if efforts is None or speeds is None:
        return None

    listed = visibility == "list"
    return {
        "backend": "codex",
        "model": slug,
        "display_name": display_name,
        "efforts": efforts,
        "default_effort": default_effort if default_effort in efforts else None,
        "speeds": speeds,
        "source": SOURCE_CACHE,
        "source_observed_at": observed_at,
        "cli_version": cli_version,
        "status": STATUS_ADVERTISED if listed else STATUS_INELIGIBLE,
        "ineligible_reason": None if listed else NOT_LISTED_REASON,
    }


def read_codex(env: Mapping[str, str]) -> dict:
    path = codex_cache_path(env)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _unavailable("codex", f"no model cache at {path}")
    except OSError:
        return _unavailable("codex", f"unreadable model cache at {path}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _unavailable("codex", f"model cache is not JSON: {path}")

    if not isinstance(document, dict) or not isinstance(document.get("models"), list):
        return _unavailable("codex", f"non-conforming model cache: {path}")

    observed_at = _text(document.get("fetched_at"))
    cli_version = _text(document.get("client_version"))
    models: list[dict] = []
    skipped = 0
    for entry in document["models"]:
        projected = _project_codex_entry(entry, observed_at, cli_version)
        if projected is None:
            skipped += 1
        else:
            models.append(projected)

    return {
        "backend": "codex",
        "source": SOURCE_CACHE,
        "source_observed_at": observed_at,
        "cli_version": cli_version,
        "skipped_entries": skipped,
        "models": models,
    }


def read_claude(_env: Mapping[str, str]) -> dict:
    """Report the Claude cache as unread.

    The backend declares its cache path in `claude_cache_path`, and no reader is
    applied to it: no schema for that document has been verified, and projecting
    it with another backend's reader would invent a claim. The report is the
    same whether or not a file exists at the path.
    """
    return _unavailable("claude", CLAUDE_UNVERIFIED_REASON)


READERS = {"claude": read_claude, "codex": read_codex}


def _select_model(backend: dict, model: str) -> dict:
    matching = [line for line in backend["models"] if line["model"] == model]
    if not matching:
        reason = backend.get("unavailable_reason")
        detail = f" ({reason})" if reason else ""
        raise ModelSelectionError(
            f"unknown model for {backend['backend']}: {model}{detail}"
        )
    return {**backend, "models": matching}


def _profile(status: ProfileStatus, reason: str, source: str) -> ProfileVerdict:
    return {"status": status, "reason": reason, "inventory_source": source}


def validate_profile(
    *,
    backend: str,
    model: str | None,
    effort: str | None,
    speed: str = DEFAULT_SPEED,
    env: Mapping[str, str] | None = None,
) -> ProfileVerdict:
    """Compare a requested inference profile to the local inventory.

    The comparison reads the same projected cache the `models` command reports,
    so it stays a local reading and never becomes an authority on what an
    account may use: only a cache that lists the model can contradict the
    request, and it contradicts it only about the effort or the speed it
    itself declares.
    """
    environment = os.environ if env is None else env
    if backend not in BACKEND_NAMES:
        raise ModelSelectionError(f"unknown backend: {backend}")

    projected = READERS[backend](environment)
    source = projected["source"]
    if source == SOURCE_UNAVAILABLE:
        return _profile(
            PROFILE_UNVERIFIED,
            f"the {backend} inventory is unavailable: "
            f"{projected['unavailable_reason']}",
            source,
        )
    if model is None:
        return _profile(
            PROFILE_UNVERIFIED,
            f"no model was requested, so {backend} applies its own default",
            source,
        )

    entry = next(
        (line for line in projected["models"] if line["model"] == model), None
    )
    if entry is None:
        return _profile(
            PROFILE_UNVERIFIED,
            f"the {backend} inventory does not list {model}",
            source,
        )

    requested = [("effort", effort, entry["efforts"])] if effort is not None else []
    requested.append(("speed", speed, entry["speeds"]))
    missing = [
        f"{name} {value}"
        for name, value, listed in requested
        if value not in listed
    ]
    if missing:
        return _profile(
            PROFILE_UNSUPPORTED,
            f"the {backend} inventory lists {model} without {' or '.join(missing)}",
            source,
        )
    carried = ", ".join(f"{name} {value}" for name, value, _ in requested)
    return _profile(
        PROFILE_SUPPORTED,
        f"the {backend} inventory lists {model} with {carried}",
        source,
    )


def build_inventory(
    *,
    actuator: str | None = None,
    model: str | None = None,
    env: Mapping[str, str] | None = None,
) -> dict:
    environment = os.environ if env is None else env
    if actuator is not None and actuator not in BACKEND_NAMES:
        raise ModelSelectionError(f"unknown backend: {actuator}")
    if model is not None and actuator is None:
        raise ModelSelectionError(
            "--model reports one backend's line, so it requires --actuator"
        )

    names = BACKEND_NAMES if actuator is None else (actuator,)
    backends = [READERS[name](environment) for name in names]
    if model is not None:
        backends = [_select_model(backends[0], model)]

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "backends": backends,
    }


def render_document(document: dict) -> str:
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def write_inventory(state_dir: Path | None, text: str) -> Path:
    root = (
        state_dir.expanduser().resolve()
        if state_dir is not None
        else (Path.home() / ".codeservo").resolve()
    )
    path = root / "models" / "inventory.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _backend_line(backend: dict) -> str:
    if backend["source"] == SOURCE_UNAVAILABLE:
        return f"{backend['backend']}: unavailable, {backend.get('unavailable_reason')}"
    parts = [f"{backend['backend']}: {backend['source']}"]
    parts.append(f"observed {backend['source_observed_at'] or 'unknown'}")
    parts.append(f"cli {backend['cli_version'] or 'unknown'}")
    if backend["skipped_entries"]:
        parts.append(f"{backend['skipped_entries']} skipped")
    return "  ".join(parts)


def _model_line(model: dict) -> str:
    efforts = ",".join(model["efforts"]) or "none"
    if model["default_effort"]:
        efforts = f"{efforts} (default {model['default_effort']})"
    parts = [
        f"  {model['model']}",
        model["display_name"] or "",
        model["status"],
        f"efforts {efforts}",
        f"speeds {','.join(model['speeds'])}",
    ]
    return "  ".join(part for part in parts if part)


def render_listing(document: dict) -> str:
    lines: list[str] = []
    for backend in document["backends"]:
        lines.append(_backend_line(backend))
        lines.extend(_model_line(model) for model in backend["models"])
    return "".join(f"{line}\n" for line in lines)
