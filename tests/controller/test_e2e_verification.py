"""A criterion that names its verification, from the task file to the decision."""

import tempfile
import unittest
from pathlib import Path

from codeservo.controller.errors import ControlFailure
from harness import build_case
from isolation_harness import requires_a_mechanism

REVIEWED_AND_GATED = """# Task

## Acceptance criteria
- [AC1] `value()` returns `2`. {review}
- [AC2] The candidate satisfies the acceptance sensor. {gate: task-outcome}
"""
NAMING_AN_ABSENT_GATE = """# Task

## Acceptance criteria
- [AC1] `value()` returns `2`. {gate: mutation}
"""


@requires_a_mechanism
class CriterionVerificationE2ETests(unittest.TestCase):
    def test_the_reviewer_is_asked_only_about_the_criteria_left_to_it(self) -> None:
        """The scripted reviewer answers about AC1 alone, and that is complete.

        Before a criterion could name a gate, a review omitting one of the two
        was a broken sensor and ended the run.
        """
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(ACCEPTABLE)")
            case.task.write_text(REVIEWED_AND_GATED, encoding="utf-8")

            result = case.run(max_iterations=1)

            self.assertEqual("ACCEPTED", result["status"])
            self.assertEqual([], result["decision"]["reasons"])
            iteration = result["iterations"][-1]
            prompt = Path(iteration["review"]["prompt"]["path"]).read_text(
                encoding="utf-8"
            )
            self.assertIn(
                "ACCEPTANCE CRITERIA YOU DECIDE\n"
                "==============================\n"
                "- AC1: `value()` returns `2`.\n",
                prompt,
            )
            self.assertIn(
                "- AC2 (gate task-outcome): The candidate satisfies the"
                " acceptance sensor.\n",
                prompt,
            )
            self.assertEqual(
                ["AC1"], [item["id"] for item in iteration["review"]["result"]["criteria"]]
            )

    def test_a_failing_gate_names_the_criterion_it_leaves_unsatisfied(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(UNACCEPTABLE)")
            case.task.write_text(REVIEWED_AND_GATED, encoding="utf-8")

            result = case.run(max_iterations=1)

            self.assertEqual("REJECTED", result["status"])
            self.assertEqual(
                [
                    "did not converge within 1 iterations",
                    "quick gate task-outcome failed: AC2 not satisfied",
                ],
                result["decision"]["reasons"],
            )
            iteration = result["iterations"][-1]
            self.assertIn(
                "Acceptance criteria this gate decides: AC2",
                iteration["controller_feedback"]["text"],
            )
            # The gate decided, so the reviewer was never invoked about it.
            self.assertNotIn("review", iteration)
            prompt = Path(iteration["prompt"]["path"]).read_text(encoding="utf-8")
            self.assertIn("- AC1 (verified by review): ", prompt)
            self.assertIn("- AC2 (verified by gate task-outcome): ", prompt)

    def test_a_criterion_naming_an_undeclared_gate_ends_the_run_before_it_starts(
        self,
    ) -> None:
        """Two control inputs held to each other, before either is frozen."""
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(ACCEPTABLE)")
            case.task.write_text(NAMING_AN_ABSENT_GATE, encoding="utf-8")

            with self.assertRaises(ControlFailure) as refused:
                case.run(max_iterations=1)

            self.assertEqual(
                "criterion AC1 names gate mutation,"
                " which the constitution does not declare",
                str(refused.exception),
            )
            self.assertFalse((case.state_dir / "runs").exists())


if __name__ == "__main__":
    unittest.main()
