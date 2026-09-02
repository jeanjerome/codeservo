"""What a document a run writes is, wherever the run writes one.

A document is a frozen dataclass: it is constructed once with every field it
declares, a field that is misnamed or missing is refused where it is written,
and nothing edits it afterwards. `to_document` renders it as the JSON object
a record, a journal line or a digest is taken over, reading the fields from
the shape rather than from a second list kept in step by hand.

Absence and a measured null are different statements, and a record has to
keep them apart. `UNSET` says a field has nothing to report and leaves it out
of the document entirely; `None` says a measurement was made and answered
nothing. Filling an absent field with null would make the record assert
something no measurement produced, which is the fault the `observed` and
`provenance` pair already exists to avoid.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any, Final


class Unset:
    """A field with nothing to report, as opposed to one measured as null.

    The marker is a type rather than a value of one, because the only
    question ever asked about it is whether a field carries it, and a type
    checker narrows that question where a bare singleton leaves the field's
    own type in place.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Final = Unset()


def rendered(value: Any) -> Any:
    """One value as a document holds it.

    A nested document renders the same way, a sequence becomes an array, and
    a mapping is rebuilt so that a document inside one is rendered too. Every
    other value is already what JSON carries, a vocabulary member included:
    it is a string, and it serialises as the string it is.
    """
    if isinstance(value, Document):
        return value.to_document()
    if isinstance(value, Mapping):
        return {key: rendered(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [rendered(item) for item in value]
    return value


@dataclass(frozen=True)
class Document:
    """A document with a frozen shape, and nothing about where it is written.

    Inheriting from a frozen dataclass is what makes every document frozen:
    Python refuses a mutable dataclass built on this one, so immutability is
    a property of the base rather than a convention each shape repeats.
    """

    def to_document(self) -> dict[str, Any]:
        """This document as the JSON object a record holds.

        Fields carrying `UNSET` are left out. Everything else is rendered,
        so a document reached through a field, a list or a mapping is
        rendered with it.
        """
        return {
            field.name: rendered(value)
            for field, value in (
                (field, getattr(self, field.name)) for field in fields(self)
            )
            if not isinstance(value, Unset)
        }
