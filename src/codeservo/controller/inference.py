"""The inference profile of each role: requested, checked, then observed.

A run has two independent roles. Each carries the profile it was asked for,
what the local inventory of its own backend could say about that request, and
what the backend then reported about itself. The three are never mixed: a
record repeating the request reads as a measurement of something nobody
measured.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..actuators.inventory import PROFILE_UNSUPPORTED, validate_profile

# What a run records about the profile a backend applied to itself, and the two
# statements it makes per field. A backend's own output either carried the
# value or it did not; a field nobody could read and a field the backend does
# not talk about are the same absence, and say the same thing.
OBSERVED_FIELDS = ("model", "effort", "speed")
REPORTED = "reported"
NOT_REPORTED = "not_reported"


@dataclass(frozen=True)
class InferenceRequest:
    """One role's requested inference profile, as the run resolved it."""

    backend: str
    model: str | None
    effort: str | None
    speed: str


def observed_profile(observed: dict) -> dict:
    """What a backend reported about its own profile, and why the rest is empty.

    Only the backend's own output speaks here. A value it did not carry stays
    empty and says so, field by field, instead of being filled from the request
    or from the command line built out of it: a record repeating what was asked
    for reads as a measurement of something nobody measured, which is worse
    than a record saying nothing. The two blocks are built together, so
    `observed` is non-null exactly where `provenance` says `reported`.
    """
    reported: dict = {}
    provenance: dict = {}
    for name in OBSERVED_FIELDS:
        value = observed.get(name)
        carried = isinstance(value, str) and bool(value)
        reported[name] = value if carried else None
        provenance[name] = REPORTED if carried else NOT_REPORTED
    return {"observed": reported, "provenance": provenance}


def frozen_profile(request: InferenceRequest) -> dict:
    """Freeze one requested inference profile before anything actuates.

    The request is recorded as it was resolved, next to what the local
    inventory of that same backend can say about it. Nothing the backend
    answers is filled in here, so a substitution can never be read back as the
    configuration asked for.
    """
    return {
        "requested": {
            "backend": request.backend,
            "model": request.model,
            "effort": request.effort,
            "speed": request.speed,
        },
        "validation": validate_profile(
            backend=request.backend,
            model=request.model,
            effort=request.effort,
            speed=request.speed,
        ),
        "native": None,
        **observed_profile({}),
    }


def frozen_inference(
    *, implementer: InferenceRequest, reviewer: InferenceRequest
) -> dict:
    """Freeze the two requested inference profiles of a run.

    The roles are independent control inputs: each is checked against the
    inventory of its own backend, so one backend's cache never answers for the
    other's.
    """
    return {
        "implementer": frozen_profile(implementer),
        "reviewer": frozen_profile(reviewer),
    }


def record_actuation(profile: dict, agent: dict) -> None:
    """Keep the profile of the last actuation, replacing any earlier one.

    The adapter owns what its backend reports and how it read it; the shape of
    the block is owned here, so the record holds the same three fields whichever
    backend answered, and nothing an adapter did not report.
    """
    profile["native"] = agent["native"]
    profile.update(observed_profile(agent["observed"]))


def contradicted_profiles(inference: dict) -> list[str]:
    """Roles whose request the inventory of their own backend contradicts."""
    return [
        f"configuration error: {role} profile: {profile['validation']['reason']}"
        for role, profile in inference.items()
        if profile["validation"]["status"] == PROFILE_UNSUPPORTED
    ]
