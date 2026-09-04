"""A run that ends with a question no control answers."""

import json
import tempfile
import unittest
from pathlib import Path

from codeservo.evidence.verify import verify_run
from harness import build_case
from isolation_harness import requires_a_mechanism

UNVERIFIABLE = """
emit_review(
    {
        "criteria": [
            {"id": "AC1", "status": "not_verifiable", "evidence": "no test names it"}
        ],
        "findings": [],
    }
)
"""
OBJECTING = """
emit_review(
    {
        "criteria": [
            {"id": "AC1", "status": "not_satisfied", "evidence": "still wrong"}
        ],
        "findings": [
            {
                "severity": "major",
                "path": "app.py",
                "line": 2,
                "message": "returns the wrong value",
                "evidence": "app.py:2",
            }
        ],
    }
)
"""
REVIEWED_AND_GATED = """# Task

## Acceptance criteria
- [AC1] `value()` returns `2`. {review}
- [AC2] The candidate satisfies the acceptance sensor. {gate: task-outcome}
"""
CONTRADICTING = """
emit_review(
    {
        "criteria": [
            {"id": "AC1", "status": "satisfied", "evidence": "app.py returns 2"},
            {"id": "AC2", "status": "not_satisfied", "evidence": "I doubt the sensor"},
        ],
        "findings": [],
    }
)
"""
TWO_REVIEWED = """# Task

## Acceptance criteria
- [AC1] `value()` returns `2`.
- [AC2] A test names `value()`.
"""
COUNTING_IMPLEMENTER = """
count = worktree / "attempts.txt"
attempts = int(count.read_text()) + 1 if count.exists() else 1
count.write_text(str(attempts))
implement(ACCEPTABLE)
"""
CORRECTED_THEN_VERIFIED = """
count = worktree / "attempts.txt"
attempts = int(count.read_text()) if count.exists() else 0
if attempts >= 2:
    emit_review(
        {
            "criteria": [
                {"id": "AC1", "status": "satisfied", "evidence": "app.py"},
                {"id": "AC2", "status": "satisfied", "evidence": "test_app.py"},
            ],
            "findings": [],
        }
    )
else:
    emit_review(
        {
            "criteria": [
                {"id": "AC1", "status": "not_satisfied", "evidence": "returns 1"},
                {"id": "AC2", "status": "not_verifiable", "evidence": "no test yet"},
            ],
            "findings": [],
        }
    )
"""


def _events(result: dict) -> list[dict]:
    return [
        json.loads(line)
        for line in Path(result["run_dir"], "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]


@requires_a_mechanism
class EscalationE2ETests(unittest.TestCase):
    def test_a_criterion_nobody_can_verify_ends_the_run_with_budget_left(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp), implementer="implement(ACCEPTABLE)", reviewer=UNVERIFIABLE
            )

            result = case.run(max_iterations=3)

            self.assertEqual("ESCALATED", result["status"])
            self.assertEqual(
                ["criterion AC1 is not_verifiable"], result["decision"]["reasons"]
            )
            # One iteration, fully measured, and nothing fed back: there is
            # nothing for the actuator to correct.
            self.assertEqual(1, len(result["iterations"]))
            iteration = result["iterations"][-1]
            self.assertTrue(all(g["passed"] for g in iteration["quick_gates"]))
            self.assertTrue(all(g["passed"] for g in iteration["full_gates"]))
            self.assertIn("result", iteration["review"])
            self.assertIsNone(iteration["controller_feedback"])
            self.assertFalse(
                Path(result["run_dir"], "iterations", "01", "controller-feedback.md").exists()
            )
            # The journal closes on the same decision, and no budget ran out.
            events = _events(result)
            self.assertEqual(
                ["review.finished", "review.profile_observed", "decision.recorded", "run.finished"],
                [event["type"] for event in events[-4:]],
            )
            self.assertEqual(
                {"status": "ESCALATED", "reasons": result["decision"]["reasons"]},
                events[-2]["payload"],
            )
            self.assertEqual({"status": "ESCALATED"}, events[-1]["payload"])
            run_dir = Path(result["run_dir"])
            self.assertEqual("VALID", verify_run(run_dir)["status"])
            evidence = json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
            self.assertEqual("ESCALATED", evidence["status"])
            self.assertEqual(19, evidence["schema_version"])
            self.assertTrue((run_dir / "change.patch").is_file())

    def test_a_review_contradicting_a_gate_that_passed_is_escalated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp), implementer="implement(ACCEPTABLE)", reviewer=CONTRADICTING
            )
            case.task.write_text(REVIEWED_AND_GATED, encoding="utf-8")

            result = case.run(max_iterations=2)

            self.assertEqual("ESCALATED", result["status"])
            self.assertEqual(
                ["review contradicts gate task-outcome on criterion AC2"],
                result["decision"]["reasons"],
            )
            self.assertEqual(1, len(result["iterations"]))
            self.assertIsNone(result["iterations"][-1]["controller_feedback"])

    def test_an_unverifiable_criterion_waits_for_the_correction_beside_it(self) -> None:
        """What the candidate is corrected for is fed back, and measured again."""
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer=COUNTING_IMPLEMENTER,
                reviewer=CORRECTED_THEN_VERIFIED,
            )
            case.task.write_text(TWO_REVIEWED, encoding="utf-8")

            result = case.run(max_iterations=3)

            self.assertEqual("ACCEPTED", result["status"])
            first, second = result["iterations"]
            text = first["controller_feedback"]["text"]
            self.assertIn("- AC1 (not_satisfied): returns 1", text)
            self.assertIn("- AC2 (not_verifiable): no test yet", text)
            self.assertEqual(text, second["feedback_received"])
            self.assertIsNone(second["controller_feedback"])

    def test_a_budget_spent_on_review_objections_alone_is_escalated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp), implementer="implement(ACCEPTABLE)", reviewer=OBJECTING
            )

            result = case.run(max_iterations=2)

            self.assertEqual("ESCALATED", result["status"])
            self.assertEqual(
                [
                    "did not converge within 2 iterations",
                    "criterion AC1 is not_satisfied",
                    "major finding: returns the wrong value",
                ],
                result["decision"]["reasons"],
            )
            self.assertEqual(2, len(result["iterations"]))
            for iteration in result["iterations"]:
                self.assertTrue(all(g["passed"] for g in iteration["quick_gates"]))
                self.assertTrue(all(g["passed"] for g in iteration["full_gates"]))
                self.assertIn("result", iteration["review"])
                self.assertIn(
                    "Review did not accept the candidate.",
                    iteration["controller_feedback"]["text"],
                )
            self.assertEqual(
                ["budget.exhausted", "decision.recorded", "run.finished"],
                [event["type"] for event in _events(result)[-3:]],
            )
            self.assertEqual("VALID", verify_run(Path(result["run_dir"]))["status"])

    def test_a_budget_spent_on_a_gate_is_rejected(self) -> None:
        """A deterministic control refused: no person is asked."""
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(UNACCEPTABLE)")

            result = case.run(max_iterations=2)

            self.assertEqual("REJECTED", result["status"])
            self.assertEqual(
                "did not converge within 2 iterations",
                result["decision"]["reasons"][0],
            )
            self.assertNotIn("review", result["iterations"][-1])


if __name__ == "__main__":
    unittest.main()
