"""The model catalogue: what a run may name, and what its tokens are rated at.

One document, owned by this package and published beside its schemas, lists
every model a run may request, the backend that drives it, and the list prices
its tokens are rated at. Nothing here reads a provider cache, starts a CLI or
asks an account what it may use: a model is one the catalogue names or it is
refused by name, and a cost is the catalogue's arithmetic over what a stream
reported, comparable across backends because the same arithmetic rates both.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..resources import model_catalogue

# The one cache-write price a backend that names no duration is rated at.
DEFAULT_WRITE = "default"
TOKENS_PER_PRICE_UNIT = 1_000_000


class Backend(StrEnum):
    """The backends a run may drive, in either role."""

    CLAUDE = "claude"
    CODEX = "codex"


class Effort(StrEnum):
    """The four reasoning efforts a run may request, handed to a CLI unchanged.

    Whether a model supports one is the CLI's to decide: an unsupported
    combination fails there, explicitly, and nothing here substitutes another.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class CatalogueError(ValueError):
    """The catalogue cannot be read, or does not name what was asked."""


@dataclass(frozen=True)
class Price:
    """List prices in USD per million tokens, one per category a stream reports.

    Cache writes are keyed by the duration the backend reports, or by
    `default` for a backend that names none. A category priced nowhere is a
    category this catalogue cannot rate, and a cost over it stays unknown.
    """

    input: float
    cached_input: float
    cache_write: Mapping[str, float]
    output: float


@dataclass(frozen=True)
class Model:
    """One model a run may request."""

    backend: Backend
    id: str
    positioning: str
    price: Price | None
    source: str | None


@dataclass(frozen=True)
class Catalogue:
    path: Path
    raw_text: str
    version: int
    priced_at: str | None
    basis: str
    models: tuple[Model, ...]

    def models_for(self, backend: Backend) -> tuple[Model, ...]:
        return tuple(model for model in self.models if model.backend == backend)

    def find(self, backend: Backend, model_id: str) -> Model | None:
        """The catalogue's entry for one model of one backend, if it has one."""
        for model in self.models:
            if model.backend == backend and model.id == model_id:
                return model
        return None

    def lookup(self, backend: Backend, model_id: str) -> Model:
        """The entry for one model, or the refusal that names what was asked.

        A model the catalogue lists for the other backend is refused as such,
        because a Codex model is driven by Codex alone and a Claude model by
        Claude alone; one it lists for neither is unknown.
        """
        found = self.find(backend, model_id)
        if found is not None:
            return found
        elsewhere = [model.backend for model in self.models if model.id == model_id]
        if elsewhere:
            raise CatalogueError(
                f"{model_id} is a {elsewhere[0]} model and cannot be driven by {backend}"
            )
        known = ", ".join(model.id for model in self.models_for(backend))
        raise CatalogueError(
            f"the catalogue names no {backend} model {model_id!r}; it names {known}"
        )


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise CatalogueError(f"{what} must be a non-negative number")
    return float(value)


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogueError(f"{what} must be a non-empty string")
    return value


def _optional_text(value: Any, what: str) -> str | None:
    return None if value is None else _text(value, what)


def _price(item: Any, what: str) -> Price | None:
    if item is None:
        return None
    if not isinstance(item, dict):
        raise CatalogueError(f"{what} must be a table")
    writes = item.get("cache_write", {})
    if not isinstance(writes, dict) or not writes:
        raise CatalogueError(f"{what}: cache_write must be a table naming at least one duration")
    return Price(
        input=_number(item.get("input"), f"{what}: input"),
        cached_input=_number(item.get("cached_input"), f"{what}: cached_input"),
        cache_write={
            str(duration): _number(price, f"{what}: cache_write.{duration}")
            for duration, price in writes.items()
        },
        output=_number(item.get("output"), f"{what}: output"),
    )


def _model(item: Any) -> Model:
    if not isinstance(item, dict):
        raise CatalogueError("each [[model]] must be a table")
    model_id = _text(item.get("id"), "model id")
    what = f"model {model_id}"
    backend = item.get("backend")
    try:
        driven_by = Backend(backend if isinstance(backend, str) else "")
    except ValueError:
        known = ", ".join(Backend)
        raise CatalogueError(f"{what}: backend must be one of {known}, not {backend!r}") from None
    return Model(
        backend=driven_by,
        id=model_id,
        positioning=_text(item.get("positioning"), f"{what}: positioning"),
        price=_price(item.get("price_per_million_tokens"), f"{what}: price_per_million_tokens"),
        source=_optional_text(item.get("source"), f"{what}: source"),
    )


def read_catalogue(path: Path) -> Catalogue:
    """Read one catalogue document, or refuse it by name."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CatalogueError(f"catalogue is not readable as text: {path}: {exc}") from None
    try:
        document = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise CatalogueError(f"catalogue is not readable as TOML: {path}: {exc}") from None
    version = document.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise CatalogueError("catalogue: version must be a positive integer")
    items = document.get("model", [])
    if not isinstance(items, list) or not items:
        raise CatalogueError("catalogue must declare at least one [[model]]")
    models = tuple(_model(item) for item in items)
    seen: set[tuple[Backend, str]] = set()
    for model in models:
        if (model.backend, model.id) in seen:
            raise CatalogueError(f"catalogue names {model.id} twice for {model.backend}")
        seen.add((model.backend, model.id))
    return Catalogue(
        path=path,
        raw_text=raw,
        version=version,
        priced_at=_optional_text(document.get("priced_at"), "catalogue: priced_at"),
        basis=_text(document.get("basis"), "catalogue: basis"),
        models=models,
    )


def load_catalogue(path: Path | None = None) -> Catalogue:
    """The published catalogue, or the one a path names."""
    return read_catalogue(path if path is not None else model_catalogue())


def rate(price: Price, tokens: Mapping[str, int | None], write_duration: str | None) -> float | None:
    """The list cost of what a stream reported, or nothing where it cannot be rated.

    Every category is rated or the whole is not: a count the stream did not
    report, or a cache-write duration the price table has no line for, leaves
    the cost unknown rather than understated. Reasoning tokens are a detail of
    the output tokens both backends already count, and are not rated twice.
    """
    total = 0.0
    for category, unit_price in (
        ("input", price.input),
        ("cached_input", price.cached_input),
        ("output", price.output),
    ):
        count = tokens.get(category)
        if count is None:
            return None
        total += count * unit_price
    writes = tokens.get("cache_write")
    if writes is None:
        return None
    if writes:
        write_price = price.cache_write.get(write_duration or DEFAULT_WRITE)
        if write_price is None:
            return None
        total += writes * write_price
    return round(total / TOKENS_PER_PRICE_UNIT, 6)
