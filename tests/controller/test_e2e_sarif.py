"""A gate whose tool writes SARIF, from the constitution to the feedback."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from codeservo.evidence.digests import sha256_file
from codeservo.evidence.verify import verify_run
from e2e_support import sarif_report, writes_report
from harness import build_case, constitution

REPORTS = "reports/*.sarif"


@unittest.skipUnless(
    sys.platform == "darwin",
    "external sensor isolation requires macOS sandbox-exec",
)
class SarifGateE2ETests(unittest.TestCase):
    """The record carries the projection, and the actuator reads the results."""

    def _case(self, root: Path, command: str, **overrides):
        return build_case(
            root,
            implementer="implement(ACCEPTABLE)",
            constitution_text=constitution(
                quick_command=command,
                quick_result_format="sarif",
                quick_reports=REPORTS,
                **overrides,
            ),
        )

    def test_the_record_carries_the_projection_of_what_the_tool_wrote(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = self._case(
                Path(temp), writes_report(sarif_report(errors=0, warnings=2))
            )

            result = case.run()

            self.assertEqual("ACCEPTED", result["status"])
            evidence = json.loads(
                Path(result["run_dir"], "evidence.json").read_text(encoding="utf-8")
            )
            for gates, path in (
                (evidence["baseline"], "baseline/syntax.observation.json"),
                (
                    evidence["iterations"][-1]["quick_gates"],
                    "iterations/01/quick/syntax.observation.json",
                ),
            ):
                gate = next(g for g in gates if g["name"] == "syntax")
                self.assertEqual("sarif", gate["result_format"])
                self.assertEqual("valid", gate["observation_status"])
                self.assertIsNone(gate["observation_error"])
                self.assertEqual(path, gate["observation_path"])
                kept = Path(result["run_dir"], path)
                self.assertEqual(sha256_file(kept), gate["observation_sha256"])
                document = json.loads(kept.read_text(encoding="utf-8"))
                self.assertEqual("syntax", document["sensor"])
                self.assertEqual("passed", document["status"])
                self.assertEqual(
                    "2 results, 0 errors, 2 warnings, 0 notes from ruff 0.12.12"
                    " in 1 report",
                    document["summary"],
                )
                self.assertEqual(2, document["metrics"]["warnings"])
            # The report stays in the tree its tool wrote it in, and the
            # record verifies from what it holds.
            self.assertTrue(
                (Path(result["worktree"]) / "reports" / "lint.sarif").is_file()
            )
            self.assertEqual("VALID", verify_run(Path(result["run_dir"]))["status"])

    def test_a_failing_analysis_is_fed_back_through_its_results(self) -> None:
        # The tool reports nothing on the base and two errors once the change
        # is in, so the actuator is told which rules fired and where.
        command = (
            "if grep -q 'return 2' app.py; then "
            + writes_report(sarif_report(errors=2), exit_code=1)
            + "; else "
            + writes_report(sarif_report(errors=0))
            + "; fi"
        )
        with tempfile.TemporaryDirectory() as temp:
            case = self._case(Path(temp), command, sensor_command=None)

            result = case.run(max_iterations=1)

            self.assertEqual("REJECTED", result["status"])
            self.assertIn("quick gate syntax failed", result["decision"]["reasons"])
            text = result["iterations"][0]["controller_feedback"]["text"]
            self.assertIn("Gate syntax FAILED", text)
            self.assertIn(
                "Summary: 2 results, 2 errors, 0 warnings, 0 notes"
                " from ruff 0.12.12 in 1 report",
                text,
            )
            self.assertIn("Findings (2):", text)
            self.assertIn("- [major] app.py:1: error: error 0 in app.py", text)
            self.assertIn("- [major] app.py:2: error: error 1 in app.py", text)
            self.assertIn(
                "Metrics: errors=2 notes=0 results=2 suppressed=0 warnings=0", text
            )

    def test_a_tool_that_did_not_finish_ends_the_run(self) -> None:
        # A gate exiting zero over a report saying its tool never completed is
        # a green that measured nothing, and the run says so at the baseline.
        unfinished = json.dumps(
            {
                "version": "2.1.0",
                "runs": [
                    {
                        "tool": {"driver": {"name": "ruff", "version": "0.12.12"}},
                        "invocations": [{"executionSuccessful": False}],
                        "results": [],
                    }
                ],
            }
        )
        with tempfile.TemporaryDirectory() as temp:
            case = self._case(Path(temp), writes_report(unfinished))

            result = case.run()

            self.assertEqual("REJECTED", result["status"])
            [reason] = result["decision"]["reasons"]
            self.assertIn("sensor error: gate syntax", reason)
            self.assertIn("reports a tool run that did not complete", reason)
            # Before any checkout, and with nothing actuated.
            self.assertIsNone(result["worktree"])
            self.assertEqual([], result["iterations"])


if __name__ == "__main__":
    unittest.main()
