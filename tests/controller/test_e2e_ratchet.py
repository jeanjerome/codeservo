"""A ratchet declared in the constitution, from the baseline to the decision."""

import json
import tempfile
import unittest
from pathlib import Path

from e2e_support import toml_basic
from harness import build_case, constitution
from isolation_harness import requires_a_mechanism


def _counts_lines(metric: str) -> str:
    """A gate command reporting the line count of the module as one metric.

    The same command measures the source tree at the baseline and the
    candidate afterwards, so the two documents differ exactly where the
    change moved the count.
    """
    document = json.dumps(
        {
            "schema_version": 1,
            "sensor": "lines",
            "status": "passed",
            "summary": "the lines of app.py",
            "findings": [],
            "metrics": {metric: "@COUNT@"},
        },
        sort_keys=True,
    )
    template = document.replace('"@COUNT@"', "%s").replace('"', '\\"')
    return f'printf "{template}" "$(wc -l < app.py)" > "$CODESERVO_OBSERVATION_PATH"'


COUNTING = toml_basic(_counts_lines("lines"))
# Two lines at the baseline; the padded implementation has five.
PADDED = 'implement(ACCEPTABLE + "\\n\\n\\n")'
PADDED_THEN_TRIMMED = """
count = worktree / "attempts.txt"
attempts = int(count.read_text()) + 1 if count.exists() else 1
count.write_text(str(attempts))
implement(ACCEPTABLE if attempts >= 2 else ACCEPTABLE + "\\n\\n\\n")
"""


@requires_a_mechanism
class RatchetE2ETests(unittest.TestCase):
    def test_a_metric_moving_the_wrong_way_opens_another_iteration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer=PADDED_THEN_TRIMMED,
                constitution_text=constitution(
                    quick_command=COUNTING,
                    quick_result_format="codeservo-json",
                    quick_ratchet='{ lines = "<=" }',
                ),
            )

            result = case.run(max_iterations=2)

            self.assertEqual("ACCEPTED", result["status"])
            first, second = result["iterations"]
            # Every quick gate passed, and the candidate was not let through.
            self.assertTrue(all(g["passed"] for g in first["quick_gates"]))
            self.assertNotIn("full_gates", first)
            self.assertNotIn("review", first)
            text = first["controller_feedback"]["text"]
            self.assertEqual(
                "Gate syntax passed but broke a ratchet\n"
                "- lines 5 on the candidate, 2 on the baseline, must be <=",
                text,
            )
            self.assertEqual(text, second["feedback_received"])
            # The next attempt is told what moved, in the one line per
            # iteration and in the rule the view of the constitution carries.
            prompt = Path(second["prompt"]["path"]).read_text(encoding="utf-8")
            self.assertIn(
                "- Iteration 1: scope OK; quick gates: 2 of 2 passed;"
                " ratchet broken: syntax lines 5 vs 2",
                prompt,
            )
            self.assertIn('ratchet = { "lines" = "<=" }', prompt)
            self.assertIsNone(second["controller_feedback"])
            self.assertIn("review", second)

    def test_a_broken_ratchet_exhausts_the_budget_with_its_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer=PADDED,
                constitution_text=constitution(
                    quick_command=COUNTING,
                    quick_result_format="codeservo-json",
                    quick_ratchet='{ lines = "<=" }',
                ),
            )

            result = case.run(max_iterations=1)

            self.assertEqual("REJECTED", result["status"])
            self.assertEqual(
                [
                    "did not converge within 1 iterations",
                    "quick gate syntax ratchet broken: lines 5 on the candidate,"
                    " 2 on the baseline, must be <=",
                ],
                result["decision"]["reasons"],
            )
            # The two documents the comparison read are both in the record.
            baseline = {g["name"]: g for g in result["baseline"]}["syntax"]
            candidate = {
                g["name"]: g for g in result["iterations"][-1]["quick_gates"]
            }["syntax"]
            for gate, lines in ((baseline, 2), (candidate, 5)):
                kept = json.loads(
                    Path(result["run_dir"], gate["observation_path"]).read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual({"lines": lines}, kept["metrics"])

    def test_a_full_gate_ratchet_decides_after_the_quick_gates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer=PADDED,
                constitution_text=constitution(
                    full_command=COUNTING,
                    full_result_format="codeservo-json",
                    full_ratchet='{ lines = "<=" }',
                ),
            )

            result = case.run(max_iterations=1)

            self.assertEqual("REJECTED", result["status"])
            self.assertEqual(
                [
                    "did not converge within 1 iterations",
                    "full gate full ratchet broken: lines 5 on the candidate,"
                    " 2 on the baseline, must be <=",
                ],
                result["decision"]["reasons"],
            )
            iteration = result["iterations"][-1]
            self.assertTrue(all(g["passed"] for g in iteration["quick_gates"]))
            self.assertTrue(all(g["passed"] for g in iteration["full_gates"]))
            self.assertNotIn("review", iteration)
            self.assertEqual(
                "Gate full passed but broke a ratchet\n"
                "- lines 5 on the candidate, 2 on the baseline, must be <=",
                iteration["controller_feedback"]["text"],
            )

    def test_a_metric_moving_the_declared_way_holds(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer=PADDED,
                constitution_text=constitution(
                    quick_command=COUNTING,
                    quick_result_format="codeservo-json",
                    quick_ratchet='{ lines = ">=" }',
                ),
            )

            result = case.run(max_iterations=1)

            self.assertEqual("ACCEPTED", result["status"])
            self.assertEqual([], result["decision"]["reasons"])

    def test_a_ratchet_on_a_metric_nobody_reports_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer=PADDED,
                constitution_text=constitution(
                    quick_command=COUNTING,
                    quick_result_format="codeservo-json",
                    quick_ratchet='{ absent = "<=" }',
                ),
            )

            result = case.run(max_iterations=1)

            self.assertEqual("ACCEPTED", result["status"])
            self.assertIsNone(result["iterations"][-1]["controller_feedback"])


if __name__ == "__main__":
    unittest.main()
