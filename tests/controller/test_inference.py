"""The two inference profiles of a run: frozen, then observed."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeservo.controller.inference import (
    InferenceRequest,
    frozen_inference,
    record_actuation,
)

PROFILE_FIELDS = {"requested", "validation", "native", "observed", "provenance"}


UNREPORTED = {
    "model": "not_reported",
    "effort": "not_reported",
    "speed": "not_reported",
}


class InferenceProfileTests(unittest.TestCase):
    """Both requested profiles are frozen before anything actuates."""

    def _request(self, **overrides) -> dict:
        request = {
            "backend": "claude",
            "model": "opus",
            "effort": "high",
            "speed": "standard",
        }
        request.update(overrides)
        return request

    def _inference(self, implementer=None, reviewer=None) -> dict:
        return frozen_inference(
            implementer=InferenceRequest(**self._request(**(implementer or {}))),
            reviewer=InferenceRequest(**self._request(**(reviewer or {}))),
        )

    def _implementer(self, **overrides) -> dict:
        return self._inference(implementer=overrides)["implementer"]

    def test_holds_the_two_roles_with_the_same_five_fields(self) -> None:
        inference = self._inference()

        self.assertEqual({"implementer", "reviewer"}, set(inference))
        self.assertEqual(PROFILE_FIELDS, set(inference["implementer"]))
        self.assertEqual(PROFILE_FIELDS, set(inference["reviewer"]))

    def test_freezes_each_role_as_it_was_resolved(self) -> None:
        inference = self._inference(
            reviewer={"backend": "codex", "model": "a-model", "speed": "fast"}
        )

        self.assertEqual(
            {
                "backend": "claude",
                "model": "opus",
                "effort": "high",
                "speed": "standard",
            },
            inference["implementer"]["requested"],
        )
        self.assertEqual(
            {
                "backend": "codex",
                "model": "a-model",
                "effort": "high",
                "speed": "fast",
            },
            inference["reviewer"]["requested"],
        )

    def test_records_an_absent_review_effort_as_null(self) -> None:
        reviewer = self._inference(reviewer={"effort": None})["reviewer"]

        self.assertIsNone(reviewer["requested"]["effort"])

    def test_checks_each_role_against_the_inventory_of_its_own_backend(self) -> None:
        """One backend's inventory never answers for the other's."""
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp)
            (codex_home / "models_cache.json").write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "slug": "a-model",
                                "supported_reasoning_levels": [{"effort": "high"}],
                                "visibility": "list",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                # The same model and effort for both roles: only the backend
                # that lists them can settle the request.
                inference = self._inference(
                    implementer={"backend": "claude", "model": "a-model"},
                    reviewer={"backend": "codex", "model": "a-model"},
                )

        self.assertEqual("unverified", inference["implementer"]["validation"]["status"])
        self.assertEqual(
            "unavailable", inference["implementer"]["validation"]["inventory_source"]
        )
        self.assertEqual("supported", inference["reviewer"]["validation"]["status"])
        self.assertEqual(
            "backend-cache", inference["reviewer"]["validation"]["inventory_source"]
        )

    def test_holds_nothing_either_backend_has_answered_yet(self) -> None:
        for role, profile in self._inference().items():
            with self.subTest(role=role):
                self.assertIsNone(profile["native"])
                self.assertEqual(
                    {"model": None, "effort": None, "speed": None},
                    profile["observed"],
                )
                self.assertEqual(UNREPORTED, profile["provenance"])

    def test_freezes_the_four_requested_fields(self) -> None:
        implementer = self._implementer(speed="fast")

        self.assertEqual(
            {
                "backend": "claude",
                "model": "opus",
                "effort": "high",
                "speed": "fast",
            },
            implementer["requested"],
        )

    def test_records_an_absent_effort_as_null(self) -> None:
        self.assertIsNone(self._implementer(effort=None)["requested"]["effort"])

    def test_holds_nothing_the_backend_has_not_answered_yet(self) -> None:
        implementer = self._implementer()

        self.assertIsNone(implementer["native"])
        self.assertEqual(
            {"model": None, "effort": None, "speed": None}, implementer["observed"]
        )
        self.assertEqual(UNREPORTED, implementer["provenance"])
        # A backend with no verified cache cannot contradict the request.
        self.assertEqual("unverified", implementer["validation"]["status"])
        self.assertEqual(
            {"status", "reason", "inventory_source"}, set(implementer["validation"])
        )


class ActuationRecordTests(unittest.TestCase):
    def _profile(self) -> dict:
        return {
            "native": {"--effort": "max"},
            "observed": {"model": "claude-opus-5", "effort": None, "speed": None},
            "provenance": {
                "model": "reported",
                "effort": "not_reported",
                "speed": "not_reported",
            },
        }

    def _empty(self) -> dict:
        return {"native": None, "observed": {}, "provenance": {}}

    def _actuate(self, profile: dict, observed: dict, native: dict) -> dict:
        record_actuation(profile, {"native": native, "observed": observed})
        return profile

    def test_says_per_field_what_the_backend_reported(self) -> None:
        profile = self._actuate(
            self._empty(),
            {"model": "gpt-5.6-sol", "effort": "high", "speed": None},
            {"model_reasoning_effort": "high"},
        )

        self.assertEqual({"model_reasoning_effort": "high"}, profile["native"])
        self.assertEqual(
            {"model": "gpt-5.6-sol", "effort": "high", "speed": None},
            profile["observed"],
        )
        self.assertEqual(
            {
                "model": "reported",
                "effort": "reported",
                "speed": "not_reported",
            },
            profile["provenance"],
        )

    def test_names_the_same_three_fields_on_both_sides(self) -> None:
        for observed in (
            {},
            {"model": "claude-opus-5", "effort": None, "speed": "standard"},
            {"model": None, "effort": None, "speed": None},
        ):
            with self.subTest(observed=observed):
                profile = self._actuate(self._empty(), observed, {})

                self.assertEqual(
                    {"model", "effort", "speed"}, set(profile["observed"])
                )
                self.assertEqual(
                    set(profile["observed"]), set(profile["provenance"])
                )

    def test_agrees_field_by_field_with_what_it_holds(self) -> None:
        for observed in (
            {"model": "claude-opus-5", "effort": None, "speed": "standard"},
            {"model": None, "effort": "high", "speed": None},
            {"model": None, "effort": None, "speed": None},
            {"model": "", "effort": None, "speed": None},
        ):
            with self.subTest(observed=observed):
                profile = self._actuate(self._empty(), observed, {})

                for name, value in profile["observed"].items():
                    expected = "reported" if value is not None else "not_reported"
                    self.assertEqual(expected, profile["provenance"][name])
                self.assertLessEqual(
                    set(profile["provenance"].values()),
                    {"reported", "not_reported"},
                )

    def test_says_nothing_was_reported_when_nothing_was_read(self) -> None:
        """A stream naming no field and no stream at all say the same thing."""
        unread = self._actuate(self._empty(), {}, {})
        silent = self._actuate(
            self._empty(), {"model": None, "effort": None, "speed": None}, {}
        )

        self.assertEqual(unread, silent)
        self.assertEqual(UNREPORTED, unread["provenance"])

    def test_keeps_no_value_from_an_earlier_actuation(self) -> None:
        profile = self._actuate(
            self._profile(), {"model": None, "effort": None, "speed": None}, {}
        )

        self.assertEqual({}, profile["native"])
        self.assertEqual(
            {"model": None, "effort": None, "speed": None}, profile["observed"]
        )
        self.assertEqual(UNREPORTED, profile["provenance"])


if __name__ == "__main__":
    unittest.main()
