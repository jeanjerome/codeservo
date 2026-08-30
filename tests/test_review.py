import unittest

from codeservo.controller import _review_decision


class ReviewDecisionTests(unittest.TestCase):
    def test_accepts_complete_satisfied_review(self):
        review = {
            "criteria": [{"id": "AC1", "status": "satisfied", "evidence": "test"}],
            "findings": [],
        }
        self.assertEqual(_review_decision(review, {"AC1": "x"}, ("blocker", "major")), [])

    def test_blocks_missing_criterion_and_major_finding(self):
        review = {
            "criteria": [],
            "findings": [{"severity": "major", "message": "bug"}],
        }
        reasons = _review_decision(review, {"AC1": "x"}, ("blocker", "major"))
        self.assertIn("review missing criterion AC1", reasons)
        self.assertIn("major finding: bug", reasons)


if __name__ == "__main__":
    unittest.main()
