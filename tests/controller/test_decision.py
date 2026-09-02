import json
import unittest

from codeservo.controller.decision import review_decision

BLOCKING = ("blocker", "major")


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
        self.assertEqual(review_decision(review, {"AC1": "x"}, BLOCKING), [])

    def test_accepts_a_satisfied_review_that_came_from_a_document(self):
        review = _reported(
            {
                "criteria": [{"id": "AC1", "status": "satisfied", "evidence": "t"}],
                "findings": [],
            }
        )
        self.assertEqual(review_decision(review, {"AC1": "x"}, BLOCKING), [])

    def test_blocks_missing_criterion_and_major_finding(self):
        review = {
            "criteria": [],
            "findings": [{"severity": "major", "message": "bug"}],
        }
        reasons = review_decision(review, {"AC1": "x"}, BLOCKING)
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
                reasons = review_decision(review, {"AC1": "x"}, BLOCKING)
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
        reasons = review_decision(review, {"AC1": "x", "AC2": "y"}, BLOCKING)
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
        reasons = review_decision(review, {"AC1": "x"}, BLOCKING)
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
        reasons = review_decision(review, {"AC1": "x"}, BLOCKING)
        self.assertIn("review duplicated criterion AC1", reasons)

    def test_a_finding_below_the_blocking_severities_is_not_a_reason(self):
        review = _reported(
            {
                "criteria": [{"id": "AC1", "status": "satisfied"}],
                "findings": [{"severity": "minor", "message": "taste"}],
            }
        )
        self.assertEqual(review_decision(review, {"AC1": "x"}, BLOCKING), [])


if __name__ == "__main__":
    unittest.main()
