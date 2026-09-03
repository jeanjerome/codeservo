"""A candidate corrected after the full gates or the review decided against it."""

import sys
import tempfile
import unittest
from pathlib import Path

from harness import build_case, constitution

# The implementer counts its attempts in the candidate, so a measurement can
# decide against the first and let the second through.
COUNTING_IMPLEMENTER = """
count = worktree / "attempts.txt"
attempts = int(count.read_text()) + 1 if count.exists() else 1
count.write_text(str(attempts))
implement(ACCEPTABLE)
"""
OBJECTING_THEN_SATISFIED = """
count = worktree / "attempts.txt"
attempts = int(count.read_text()) if count.exists() else 0
if attempts >= 2:
    emit_review(SATISFIED)
else:
    emit_review(
        {
            "criteria": [
                {"id": "AC1", "status": "not_satisfied", "evidence": "first attempt"}
            ],
            "findings": [
                {
                    "severity": "major",
                    "path": "app.py",
                    "line": 1,
                    "message": "not there yet",
                    "evidence": "app.py:1",
                },
                {"severity": "minor", "path": "app.py", "line": 2, "message": "style"},
            ],
        }
    )
"""
# Green on the source repository, where no attempt was made, so the baseline
# passes; red on the first attempt and green on the second.
FULL_PASSES_ON_THE_SECOND_ATTEMPT = "test ! -f attempts.txt || grep -q 2 attempts.txt"


@unittest.skipUnless(
    sys.platform == "darwin",
    "controller confinement requires macOS sandbox-exec",
)
class CorrectionAfterReviewTests(unittest.TestCase):
    def test_a_review_that_objects_opens_another_iteration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer=COUNTING_IMPLEMENTER,
                reviewer=OBJECTING_THEN_SATISFIED,
            )

            result = case.run(max_iterations=3)

            self.assertEqual("ACCEPTED", result["status"])
            self.assertEqual([], result["decision"]["reasons"])
            first, second = result["iterations"]
            # Both iterations passed every gate and were reviewed.
            for iteration in (first, second):
                self.assertTrue(all(g["passed"] for g in iteration["quick_gates"]))
                self.assertTrue(all(g["passed"] for g in iteration["full_gates"]))
                self.assertIn("result", iteration["review"])
            # The first review's objections are what the second attempt read:
            # the criterion, the blocking finding, and not the minor one.
            text = first["controller_feedback"]["text"]
            self.assertIn("Review did not accept the candidate.", text)
            self.assertIn("- AC1 (not_satisfied): first attempt", text)
            self.assertIn("- [major] app.py:1: not there yet", text)
            self.assertNotIn("style", text)
            self.assertEqual(text, second["feedback_received"])
            self.assertIsNone(second["controller_feedback"])
            prompt = Path(second["prompt"]["path"]).read_text(encoding="utf-8")
            self.assertIn(
                "- Iteration 1: scope OK; quick gates: 2 of 2 passed;"
                " full gates: 1 of 1 passed;"
                " review: 1 of 1 criteria not satisfied (AC1), 1 blocking finding",
                prompt,
            )
            # Each review has its own prompt and answer, under its iteration.
            self.assertNotEqual(first["review"]["prompt"]["path"], second["review"]["prompt"]["path"])
            self.assertTrue(Path(first["review"]["prompt"]["path"]).is_file())
            self.assertTrue(Path(second["review"]["prompt"]["path"]).is_file())

    def test_a_full_gate_that_fails_opens_another_iteration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer=COUNTING_IMPLEMENTER,
                constitution_text=constitution(
                    full_command=FULL_PASSES_ON_THE_SECOND_ATTEMPT
                ),
            )

            result = case.run(max_iterations=3)

            self.assertEqual("ACCEPTED", result["status"])
            first, second = result["iterations"]
            self.assertFalse(first["full_gates"][0]["passed"])
            self.assertNotIn("review", first)
            self.assertIn("Gate full FAILED", first["controller_feedback"]["text"])
            self.assertTrue(second["full_gates"][0]["passed"])
            self.assertIn("result", second["review"])
            prompt = Path(second["prompt"]["path"]).read_text(encoding="utf-8")
            self.assertIn(
                "- Iteration 1: scope OK; quick gates: 2 of 2 passed;"
                " full gates: 0 of 1 passed; failed: full (exit code 1)",
                prompt,
            )


if __name__ == "__main__":
    unittest.main()
