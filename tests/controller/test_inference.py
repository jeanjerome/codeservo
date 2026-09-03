"""The two inference profiles of a run: frozen, then observed."""

import unittest
from dataclasses import dataclass
from typing import Any

from codeservo.actuators.base import Billed, ObservedProfile, Tokens, Usage
from codeservo.actuators.catalogue import Backend, Effort
from codeservo.controller.document import InferenceProfile, RequestedProfile
from codeservo.controller.inference import (
    InferenceRequest,
    frozen_inference,
    record_actuation,
)

PROFILE_FIELDS = {"requested", "native", "observed", "provenance"}
UNREPORTED = {"model": "not_reported", "effort": "not_reported"}
NOTHING = Usage(billed=(), cache_write_duration=None)


@dataclass(frozen=True, kw_only=True)
class Reported:
    """What a backend answered, carrying the three things the port reads."""

    native: dict[str, Any]
    observed: ObservedProfile
    usage: Usage = NOTHING


class InferenceProfileTests(unittest.TestCase):
    """Both requested profiles are frozen before anything actuates."""

    def _request(self, **overrides) -> dict:
        request = {
            "backend": Backend.CLAUDE,
            "model": "claude-opus-5",
            "effort": Effort.HIGH,
        }
        request.update(overrides)
        return request

    def _inference(self, implementer=None, reviewer=None):
        return frozen_inference(
            implementer=InferenceRequest(**self._request(**(implementer or {}))),
            reviewer=InferenceRequest(**self._request(**(reviewer or {}))),
        )

    def test_holds_the_two_roles_with_the_same_four_fields(self) -> None:
        inference = self._inference()

        self.assertEqual({"implementer", "reviewer"}, set(inference.to_document()))
        self.assertEqual(PROFILE_FIELDS, set(inference.implementer.to_document()))
        self.assertEqual(PROFILE_FIELDS, set(inference.reviewer.to_document()))

    def test_freezes_each_role_as_it_was_resolved(self) -> None:
        inference = self._inference(
            reviewer={"backend": Backend.CODEX, "model": "gpt-5.6-sol", "effort": Effort.LOW}
        )

        self.assertEqual(
            {"backend": "claude", "model": "claude-opus-5", "effort": "high"},
            inference.implementer.requested.to_document(),
        )
        self.assertEqual(
            {"backend": "codex", "model": "gpt-5.6-sol", "effort": "low"},
            inference.reviewer.requested.to_document(),
        )

    def test_holds_nothing_either_backend_has_answered_yet(self) -> None:
        for role, profile in (
            ("implementer", self._inference().implementer),
            ("reviewer", self._inference().reviewer),
        ):
            with self.subTest(role=role):
                self.assertIsNone(profile.native)
                self.assertEqual(ObservedProfile(), profile.observed)
                self.assertEqual(UNREPORTED, profile.provenance)


class ActuationRecordTests(unittest.TestCase):
    def _blank(self, **overrides) -> InferenceProfile:
        fields: dict[str, Any] = {
            "requested": RequestedProfile(
                backend=Backend.CLAUDE, model="claude-opus-5", effort=Effort.HIGH
            ),
            "native": None,
            "observed": ObservedProfile(),
            "provenance": {},
        }
        fields.update(overrides)
        return InferenceProfile(**fields)

    def _profile(self) -> InferenceProfile:
        return self._blank(
            native={"--effort": "max"},
            observed=ObservedProfile(model="claude-opus-5"),
            provenance={"model": "reported", "effort": "not_reported"},
        )

    def _actuate(
        self, profile: InferenceProfile, observed: ObservedProfile, native: dict
    ) -> InferenceProfile:
        return record_actuation(profile, Reported(native=native, observed=observed))

    def test_says_per_field_what_the_backend_reported(self) -> None:
        profile = self._actuate(
            self._blank(),
            ObservedProfile(model="gpt-5.6-sol", effort="high"),
            {"model_reasoning_effort": "high"},
        )

        self.assertEqual({"model_reasoning_effort": "high"}, profile.native)
        self.assertEqual(ObservedProfile(model="gpt-5.6-sol", effort="high"), profile.observed)
        self.assertEqual({"model": "reported", "effort": "reported"}, profile.provenance)

    def test_names_the_same_two_fields_on_both_sides(self) -> None:
        for observed in (ObservedProfile(), ObservedProfile(model="claude-opus-5")):
            with self.subTest(observed=observed):
                profile = self._actuate(self._blank(), observed, {})

                answered = profile.observed.to_document()
                self.assertEqual({"model", "effort"}, set(answered))
                self.assertEqual(set(answered), set(profile.provenance))

    def test_agrees_field_by_field_with_what_it_holds(self) -> None:
        for observed in (
            ObservedProfile(model="claude-opus-5"),
            ObservedProfile(effort="high"),
            ObservedProfile(),
            ObservedProfile(model=""),
        ):
            with self.subTest(observed=observed):
                profile = self._actuate(self._blank(), observed, {})

                for name, value in profile.observed.to_document().items():
                    expected = "reported" if value is not None else "not_reported"
                    self.assertEqual(expected, profile.provenance[name])

    def test_says_nothing_was_reported_when_nothing_was_read(self) -> None:
        unread = self._actuate(self._blank(), ObservedProfile(), {})
        silent = self._actuate(self._blank(), ObservedProfile(model=None, effort=None), {})

        self.assertEqual(unread, silent)
        self.assertEqual(UNREPORTED, unread.provenance)

    def test_keeps_no_value_from_an_earlier_actuation(self) -> None:
        profile = self._actuate(self._profile(), ObservedProfile(), {})

        self.assertEqual({}, profile.native)
        self.assertEqual(ObservedProfile(), profile.observed)
        self.assertEqual(UNREPORTED, profile.provenance)

    def test_the_request_survives_every_actuation_untouched(self) -> None:
        profile = self._actuate(
            self._profile(),
            ObservedProfile(model="another"),
            {"--model": "another"},
        )

        self.assertEqual(
            {"backend": "claude", "model": "claude-opus-5", "effort": "high"},
            profile.requested.to_document(),
        )
        # What crosses the port is read and never copied whole: the usage is
        # rated elsewhere, and a reported profile carrying one changes nothing here.
        reported = Reported(
            native={},
            observed=ObservedProfile(),
            usage=Usage(
                billed=(
                    Billed(
                        model="claude-opus-5",
                        tokens=Tokens(input=1, cached_input=0, cache_write=0, output=1, reasoning=0),
                        reported_cost_usd=None,
                    ),
                ),
                cache_write_duration=None,
            ),
        )
        self.assertEqual(PROFILE_FIELDS, set(record_actuation(profile, reported).to_document()))


if __name__ == "__main__":
    unittest.main()
