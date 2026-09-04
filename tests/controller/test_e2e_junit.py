"""A gate whose tool writes JUnit XML, from the constitution to the feedback."""

import json
import tempfile
import unittest
from pathlib import Path

from codeservo.evidence.digests import sha256_file
from codeservo.evidence.verify import verify_run
from e2e_support import junit_report, writes_junit_report
from harness import build_case, constitution
from isolation_harness import requires_a_mechanism

REPORTS = "reports/TEST-*.xml"


@requires_a_mechanism
class JunitGateE2ETests(unittest.TestCase):
    """The record carries the projection, and the actuator reads the findings."""

    def _evidence(self, result: dict) -> dict:
        return json.loads(
            Path(result["run_dir"], "evidence.json").read_text(encoding="utf-8")
        )

    def test_the_record_carries_the_projection_of_what_the_gate_wrote(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                constitution_text=constitution(
                    quick_command=writes_junit_report(junit_report(passed=3)),
                    quick_result_format="junit-xml",
                    quick_reports=REPORTS,
                ),
            )

            result = case.run()

            self.assertEqual("ACCEPTED", result["status"])
            evidence = self._evidence(result)
            for gates, path in (
                (evidence["baseline"], "baseline/syntax.observation.json"),
                (
                    evidence["iterations"][-1]["quick_gates"],
                    "iterations/01/quick/syntax.observation.json",
                ),
            ):
                gate = next(g for g in gates if g["name"] == "syntax")
                self.assertEqual("junit-xml", gate["result_format"])
                self.assertEqual("valid", gate["observation_status"])
                self.assertIsNone(gate["observation_error"])
                self.assertEqual(path, gate["observation_path"])
                kept = Path(result["run_dir"], path)
                self.assertEqual(sha256_file(kept), gate["observation_sha256"])
                document = json.loads(kept.read_text(encoding="utf-8"))
                self.assertEqual("syntax", document["sensor"])
                self.assertEqual("passed", document["status"])
                self.assertEqual(
                    "3 tests, 0 failures, 0 errors, 0 skipped in 1 report",
                    document["summary"],
                )
                self.assertEqual(3, document["metrics"]["tests"])
            # The report itself stays in the tree its tool wrote it in, and
            # the record verifies from what it holds.
            self.assertTrue(
                (Path(result["worktree"]) / "reports" / "TEST-suite.xml").is_file()
            )
            self.assertEqual("VALID", verify_run(Path(result["run_dir"]))["status"])

    def test_a_failing_suite_is_fed_back_through_its_findings(self) -> None:
        # The gate passes on the base and fails once the change is in, so the
        # actuator is told which cases failed and what their tool said.
        command = (
            "if grep -q 'return 2' app.py; then "
            + writes_junit_report(
                junit_report(passed=1, failed=1, errors=1), exit_code=1
            )
            + "; else "
            + writes_junit_report(junit_report(passed=3))
            + "; fi"
        )
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                constitution_text=constitution(
                    quick_command=command,
                    quick_result_format="junit-xml",
                    quick_reports=REPORTS,
                    sensor_command=None,
                ),
            )

            result = case.run(max_iterations=1)

            self.assertEqual("REJECTED", result["status"])
            self.assertIn("quick gate syntax failed", result["decision"]["reasons"])
            text = result["iterations"][0]["controller_feedback"]["text"]
            self.assertIn("Gate syntax FAILED", text)
            self.assertIn(
                "Summary: 3 tests, 1 failures, 1 errors, 0 skipped in 1 report", text
            )
            self.assertIn("Findings (2):", text)
            self.assertIn("- [major] (no path): failed: expected 2 but was 0", text)
            self.assertIn("- [major] (no path): error: boom", text)
            self.assertIn(
                "Metrics: errors=1 failures=1 seconds=0.5 skipped=0 tests=3", text
            )

    def test_a_passing_gate_that_wrote_no_report_is_a_fault_of_the_sensor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                constitution_text=constitution(
                    quick_command="true",
                    quick_result_format="junit-xml",
                    quick_reports=REPORTS,
                ),
            )

            result = case.run()

            self.assertEqual("REJECTED", result["status"])
            self.assertEqual(
                [
                    "sensor error: gate syntax: the gate passed and wrote no"
                    f" report matching {REPORTS}"
                ],
                result["decision"]["reasons"],
            )
            # At the baseline: nothing was checked out, nothing actuated.
            self.assertIsNone(result["worktree"])

    def test_a_report_left_in_the_source_repository_is_not_this_measurement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                constitution_text=constitution(
                    quick_command=writes_junit_report(junit_report(passed=2)),
                    quick_result_format="junit-xml",
                    quick_reports=REPORTS,
                ),
            )
            stale = case.repo / "reports" / "TEST-old.xml"
            stale.parent.mkdir()
            stale.write_text(junit_report(suite="old", failed=5), encoding="utf-8")

            result = case.run()

            self.assertEqual("ACCEPTED", result["status"])
            baseline = next(g for g in result["baseline"] if g["name"] == "syntax")
            document = json.loads(Path(baseline["observation_path"]).read_text())
            self.assertEqual(
                "2 tests, 0 failures, 0 errors, 0 skipped in 1 report;"
                " 1 left from an earlier measurement, not read",
                document["summary"],
            )
            self.assertEqual(0, document["metrics"]["failures"])
            self.assertTrue(stale.is_file())


if __name__ == "__main__":
    unittest.main()
