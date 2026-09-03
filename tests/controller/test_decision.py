import json
import unittest

from codeservo.controller.decision import (
    review_decision,
    review_faults,
    review_feedback,
)
from codeservo.domain.task import Criterion

BLOCKING = ("blocker", "major")


def _reviewed(*ids: str) -> dict[str, Criterion]:
    """Criteria the task left to the reviewer, in the order it declared them."""
    return {
        criterion_id: Criterion(id=criterion_id, text=criterion_id.lower())
        for criterion_id in ids
    }


def _gated(gate: str, *ids: str) -> dict[str, Criterion]:
    """Criteria a gate decides, which the reviewer is never asked about."""
    return {
        criterion_id: Criterion(id=criterion_id, text=criterion_id.lower(), gate=gate)
        for criterion_id in ids
    }


def _reported(review: dict) -> dict:
    """The review as the controller receives it: read back from JSON.

    A reviewer answers with a document, so every string in it is a fresh
    object rather than the literal this file wrote. What the rules compare is
    the value, and a test passing its own literals would not say so.
    """
    return json.loads(json.dumps(review))


class ReviewDecisionTests(unittest.TestCase):
    def test_accepts_complete_satisfied_review(self):
        review = {
            "criteria": [{"id": "AC1", "status": "satisfied", "evidence": "test"}],
            "findings": [],
        }
        self.assertEqual(review_decision(review, _reviewed("AC1"), BLOCKING), [])

    def test_accepts_a_satisfied_review_that_came_from_a_document(self):
        review = _reported(
            {
                "criteria": [{"id": "AC1", "status": "satisfied", "evidence": "t"}],
                "findings": [],
            }
        )
        self.assertEqual(review_decision(review, _reviewed("AC1"), BLOCKING), [])

    def test_blocks_missing_criterion_and_major_finding(self):
        review = {
            "criteria": [],
            "findings": [{"severity": "major", "message": "bug"}],
        }
        reasons = review_decision(review, _reviewed("AC1"), BLOCKING)
        self.assertIn("review missing criterion AC1", reasons)
        self.assertIn("major finding: bug", reasons)

    def test_blocks_every_status_that_is_not_satisfied(self):
        """Whatever the reviewer answered, one word alone accepts a criterion.

        The two statuses sit either side of `satisfied` alphabetically, so an
        ordering comparison would let one of them through where an equality
        does not.
        """
        for status in ("unsatisfied", "partial"):
            with self.subTest(status=status):
                review = _reported(
                    {
                        "criteria": [{"id": "AC1", "status": status}],
                        "findings": [],
                    }
                )
                reasons = review_decision(review, _reviewed("AC1"), BLOCKING)
                self.assertIn(f"criterion AC1 is {status}", reasons)

    def test_a_criterion_the_review_omits_is_missing_and_not_unknown(self):
        """The two are different failures, and a review may earn only one.

        A criterion the task declares and the review does not report is
        missing. Calling it unknown as well would report the reviewer for
        answering about something nobody asked, which is the opposite fault.
        """
        review = _reported(
            {
                "criteria": [{"id": "AC2", "status": "satisfied"}],
                "findings": [],
            }
        )
        reasons = review_decision(review, _reviewed("AC1", "AC2"), BLOCKING)
        self.assertIn("review missing criterion AC1", reasons)
        self.assertNotIn("review returned unknown criterion AC1", reasons)

    def test_a_criterion_the_task_does_not_declare_is_unknown(self):
        review = _reported(
            {
                "criteria": [
                    {"id": "AC1", "status": "satisfied"},
                    {"id": "AC9", "status": "satisfied"},
                ],
                "findings": [],
            }
        )
        reasons = review_decision(review, _reviewed("AC1"), BLOCKING)
        self.assertEqual(reasons, ["review returned unknown criterion AC9"])

    def test_a_criterion_reported_twice_is_named(self):
        review = _reported(
            {
                "criteria": [
                    {"id": "AC1", "status": "unsatisfied"},
                    {"id": "AC1", "status": "satisfied"},
                ],
                "findings": [],
            }
        )
        reasons = review_decision(review, _reviewed("AC1"), BLOCKING)
        self.assertIn("review duplicated criterion AC1", reasons)

    def test_a_finding_below_the_blocking_severities_is_not_a_reason(self):
        review = _reported(
            {
                "criteria": [{"id": "AC1", "status": "satisfied"}],
                "findings": [{"severity": "minor", "message": "taste"}],
            }
        )
        self.assertEqual(review_decision(review, _reviewed("AC1"), BLOCKING), [])


class ReviewFaultTests(unittest.TestCase):
    """What the reviewer got wrong is a fault of the sensor, not of the candidate."""

    def test_a_complete_review_has_no_fault_whatever_it_decided(self):
        review = {
            "criteria": [{"id": "AC1", "status": "not_satisfied", "evidence": "x"}],
            "findings": [{"severity": "blocker", "message": "bad"}],
        }
        self.assertEqual([], review_faults(review, _reviewed("AC1")))

    def test_names_a_missing_a_duplicated_and_an_unknown_criterion(self):
        review = _reported(
            {
                "criteria": [
                    {"id": "AC2", "status": "satisfied"},
                    {"id": "AC2", "status": "satisfied"},
                    {"id": "AC9", "status": "satisfied"},
                ],
                "findings": [],
            }
        )
        self.assertEqual(
            [
                "review duplicated criterion AC2",
                "review missing criterion AC1",
                "review returned unknown criterion AC9",
            ],
            review_faults(review, _reviewed("AC1", "AC2")),
        )


class ReviewFeedbackTests(unittest.TestCase):
    """Only what decided against the candidate is fed back to it."""

    def test_nothing_to_feed_back_from_an_accepting_review(self):
        review = {
            "criteria": [{"id": "AC1", "status": "satisfied", "evidence": "t"}],
            "findings": [{"severity": "minor", "message": "style"}],
        }
        self.assertEqual("", review_feedback(review, _reviewed("AC1"), BLOCKING))

    def test_names_the_criteria_and_the_blocking_findings_with_their_place(self):
        review = _reported(
            {
                "criteria": [
                    {"id": "AC1", "status": "satisfied", "evidence": "fine"},
                    {"id": "AC2", "status": "not_satisfied", "evidence": "no test"},
                    {"id": "AC3", "status": "not_verifiable", "evidence": ""},
                ],
                "findings": [
                    {
                        "severity": "major",
                        "path": "app.py",
                        "line": 2,
                        "message": "ignores its caller",
                        "evidence": "app.py:2",
                    },
                    {"severity": "blocker", "path": None, "message": "corrupts"},
                    {"severity": "minor", "path": "app.py", "message": "style"},
                ],
            }
        )

        feedback = review_feedback(
            review, _reviewed("AC1", "AC2", "AC3"), BLOCKING
        )

        self.assertEqual(
            "\n".join(
                [
                    "Review did not accept the candidate.",
                    "Criteria not satisfied:",
                    "- AC2 (not_satisfied): no test",
                    "- AC3 (not_verifiable): no evidence given",
                    "Blocking findings:",
                    "- [major] app.py:2: ignores its caller",
                    "  evidence: app.py:2",
                    "- [blocker] (no path): corrupts",
                ]
            ),
            feedback,
        )
        self.assertNotIn("style", feedback)


class GateVerifiedCriterionTests(unittest.TestCase):
    """A criterion naming a gate is decided by that gate and not by the review.

    The run reaches the review only once every gate has passed, so a gate
    criterion is already satisfied when the reviewer is invoked. The rules
    here neither ask for it nor read what the reviewer volunteered about it.
    """

    CRITERIA = {**_reviewed("AC1"), **_gated("unit", "AC2")}

    def test_a_review_answering_only_what_it_was_asked_accepts(self):
        review = _reported(
            {
                "criteria": [{"id": "AC1", "status": "satisfied", "evidence": "t"}],
                "findings": [],
            }
        )

        self.assertEqual([], review_decision(review, self.CRITERIA, BLOCKING))
        self.assertEqual([], review_faults(review, self.CRITERIA))

    def test_a_criterion_a_gate_decides_is_not_missing_from_the_review(self):
        """Nothing asked the reviewer for it, so its absence is no fault."""
        review = _reported({"criteria": [], "findings": []})

        faults = review_faults(review, _gated("unit", "AC2"))
        reasons = review_decision(review, _gated("unit", "AC2"), BLOCKING)

        self.assertEqual([], faults)
        self.assertEqual([], reasons)

    def test_what_the_review_says_of_a_gate_criterion_decides_nothing(self):
        """The gate is the authority, and the answer stays in the record."""
        review = _reported(
            {
                "criteria": [
                    {"id": "AC1", "status": "satisfied", "evidence": "t"},
                    {"id": "AC2", "status": "not_satisfied", "evidence": "doubt"},
                ],
                "findings": [],
            }
        )

        self.assertEqual([], review_decision(review, self.CRITERIA, BLOCKING))
        self.assertEqual([], review_faults(review, self.CRITERIA))
        self.assertEqual("", review_feedback(review, self.CRITERIA, BLOCKING))

    def test_an_id_the_task_never_declared_is_still_unknown(self):
        review = _reported(
            {
                "criteria": [
                    {"id": "AC1", "status": "satisfied", "evidence": "t"},
                    {"id": "AC9", "status": "satisfied", "evidence": "t"},
                ],
                "findings": [],
            }
        )

        self.assertEqual(
            ["review returned unknown criterion AC9"],
            review_faults(review, self.CRITERIA),
        )

    def test_a_reviewed_criterion_beside_a_gate_one_still_decides(self):
        review = _reported(
            {
                "criteria": [
                    {"id": "AC1", "status": "not_satisfied", "evidence": "no test"}
                ],
                "findings": [],
            }
        )

        self.assertEqual(
            ["criterion AC1 is not_satisfied"],
            review_decision(review, self.CRITERIA, BLOCKING),
        )
        self.assertIn(
            "- AC1 (not_satisfied): no test",
            review_feedback(review, self.CRITERIA, BLOCKING),
        )

    def test_a_blocking_finding_still_decides_when_every_criterion_is_gated(self):
        """The reviewer keeps its other half: what it found on the tree."""
        review = _reported(
            {
                "criteria": [],
                "findings": [{"severity": "blocker", "message": "corrupts"}],
            }
        )
        criteria = _gated("unit", "AC1")

        self.assertEqual(
            ["blocker finding: corrupts"],
            review_decision(review, criteria, BLOCKING),
        )
        self.assertEqual([], review_faults(review, criteria))
        self.assertIn(
            "- [blocker] (no path): corrupts",
            review_feedback(review, criteria, BLOCKING),
        )


if __name__ == "__main__":
    unittest.main()
