"""The inference profile of each role: requested, then observed.

A run has two independent roles. Each carries the profile it was asked for
and what the backend then reported about itself. The two are never mixed: a
record repeating the request reads as a measurement of something nobody
measured. Whether a request names a model the catalogue knows is settled
before the run directory exists, so nothing here validates.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from enum import StrEnum

from ..actuators.base import ObservedProfile, ReportedProfile
from ..actuators.catalogue import Backend, Effort
from .document import Inference, InferenceProfile, RequestedProfile

# What a run records about the profile a backend applied to itself, read from
# the shape the backends answer with.
OBSERVED_FIELDS = tuple(field.name for field in fields(ObservedProfile))


class Provenance(StrEnum):
    """What a record says about one field of an observed profile.

    A backend's own output either carried the value or it did not; a field
    nobody could read and a field the backend does not talk about are the same
    absence, and say the same thing.
    """

    REPORTED = "reported"
    NOT_REPORTED = "not_reported"


@dataclass(frozen=True)
class InferenceRequest:
    """One role's requested inference profile, as the run resolved it."""

    backend: Backend
    model: str
    effort: Effort


def observed_profile(
    observed: ObservedProfile,
) -> tuple[ObservedProfile, dict[str, str]]:
    """What a backend reported about its own profile, and why the rest is empty.

    Only the backend's own output speaks here. A value it did not carry stays
    empty and says so, field by field, instead of being filled from the request
    or from the command line built out of it: a record repeating what was asked
    for reads as a measurement of something nobody measured, which is worse
    than a record saying nothing. The two blocks are built together, so
    `observed` is non-null exactly where `provenance` says `reported`.
    """
    reported: dict[str, str | None] = {}
    provenance: dict[str, str] = {}
    for name in OBSERVED_FIELDS:
        value = getattr(observed, name)
        carried = value if isinstance(value, str) and value else None
        reported[name] = carried
        provenance[name] = (
            Provenance.REPORTED if carried is not None else Provenance.NOT_REPORTED
        )
    answered = ObservedProfile(model=reported["model"], effort=reported["effort"])
    return answered, provenance


def frozen_profile(request: InferenceRequest) -> InferenceProfile:
    """Freeze one requested inference profile before anything actuates.

    Nothing the backend answers is filled in here, so a substitution can never
    be read back as the configuration asked for.
    """
    answered, provenance = observed_profile(ObservedProfile())
    return InferenceProfile(
        requested=RequestedProfile(
            backend=request.backend, model=request.model, effort=request.effort
        ),
        native=None,
        observed=answered,
        provenance=provenance,
    )


def frozen_inference(
    *, implementer: InferenceRequest, reviewer: InferenceRequest
) -> Inference:
    """Freeze the two requested inference profiles of a run."""
    return Inference(
        implementer=frozen_profile(implementer),
        reviewer=frozen_profile(reviewer),
    )


def record_actuation(
    profile: InferenceProfile, agent: ReportedProfile
) -> InferenceProfile:
    """The profile carrying the last actuation, replacing any earlier one.

    The adapter owns what its backend reports and how it read it; the shape of
    the block is owned here, so the record holds the same fields whichever
    backend answered, and nothing an adapter did not report. What was frozen
    before the run, the request, is carried through untouched.
    """
    answered, provenance = observed_profile(agent.observed)
    return replace(
        profile, native=agent.native, observed=answered, provenance=provenance
    )


def roles(inference: Inference) -> list[tuple[str, InferenceProfile]]:
    """The two roles of a run, named once, in the order a record states them."""
    return [
        ("implementer", inference.implementer),
        ("reviewer", inference.reviewer),
    ]
