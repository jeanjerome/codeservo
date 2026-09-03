"""A gate whose coverage tool writes LCOV, from the constitution to the ratchet."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from codeservo.evidence.digests import sha256_file
from codeservo.evidence.verify import verify_run
from e2e_support import lcov_report, writes_report
from harness import build_case, constitution

REPORTS = "**/*.info"
INTO = "coverage/lcov.info"


@unittest.skipUnless(
    sys.platform == "darwin",
    "external sensor isolation requires macOS sandbox-exec",
)
class LcovGateE2ETests(unittest.TestCase):
    """The record carries the projection, and a ratchet reads its metrics."""

    def _case(self, root: Path, command: str, **overrides):
        return build_case(
            root,
            implementer="implement(ACCEPTABLE)",
            constitution_text=constitution(
                full_command=command,
                full_result_format="lcov",
                full_reports=REPORTS,
                **overrides,
            ),
        )

    def test_the_record_carries_the_projection_of_what_the_tool_wrote(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = self._case(
                Path(temp),
                writes_report(lcov_report(covered=3, missing=1), into=INTO),
            )

            result = case.run()

            self.assertEqual("ACCEPTED", result["status"])
            evidence = json.loads(
                Path(result["run_dir"], "evidence.json").read_text(encoding="utf-8")
            )
            gate = next(g for g in evidence["baseline"] if g["name"] == "full")
            self.assertEqual("lcov", gate["result_format"])
            self.assertEqual("valid", gate["observation_status"])
            self.assertEqual("baseline/full.observation.json", gate["observation_path"])
            kept = Path(result["run_dir"], gate["observation_path"])
            self.assertEqual(sha256_file(kept), gate["observation_sha256"])
            document = json.loads(kept.read_text(encoding="utf-8"))
            self.assertEqual(
                "75.00 percent of 4 lines over 1 file in 1 report", document["summary"]
            )
            self.assertEqual(75.0, document["metrics"]["line_coverage"])
            self.assertEqual(1, document["metrics"]["lines_missing"])
            self.assertTrue((Path(result["worktree"]) / INTO).is_file())
            self.assertEqual("VALID", verify_run(Path(result["run_dir"]))["status"])

    def test_a_ratchet_reads_the_metrics_the_projection_carries(self) -> None:
        # Coverage falls from three lines of four to two of four once the
        # change is in, and the ratchet decides against the candidate although
        # the gate itself passed.
        command = (
            "if grep -q 'return 2' app.py; then "
            + writes_report(lcov_report(covered=2, missing=2), into=INTO)
            + "; else "
            + writes_report(lcov_report(covered=3, missing=1), into=INTO)
            + "; fi"
        )
        with tempfile.TemporaryDirectory() as temp:
            case = self._case(
                Path(temp),
                command,
                full_ratchet='{ line_coverage = ">=", lines_missing = "<=" }',
                sensor_command=None,
            )

            result = case.run(max_iterations=1)

            self.assertEqual("REJECTED", result["status"])
            reasons = " ".join(result["decision"]["reasons"])
            self.assertIn("full gate full ratchet broken", reasons)
            self.assertIn("line_coverage 50.0 on the candidate, 75.0", reasons)
            self.assertIn("lines_missing 2 on the candidate, 1", reasons)
            # A ratchet is read over a gate that passed, and this one did.
            iteration = result["iterations"][-1]
            self.assertTrue(all(g["passed"] for g in iteration["full_gates"]))

    def test_a_report_the_repository_does_not_ignore_mutates_the_tree(self) -> None:
        # The mechanism reads what a tool wrote where it always writes it, so
        # the target repository has to ignore that location: a baseline gate
        # leaving a tracked file behind has changed the tree it only measured,
        # whichever format the report is in.
        with tempfile.TemporaryDirectory() as temp:
            case = self._case(
                Path(temp), writes_report(lcov_report(), into="tracked/lcov.info")
            )

            result = case.run()

            self.assertEqual("REJECTED", result["status"])
            self.assertEqual(
                ["baseline gate mutated the source repository"],
                result["decision"]["reasons"],
            )

    def test_a_tracefile_the_tool_did_not_finish_ends_the_run(self) -> None:
        # A coverage tool killed halfway leaves a tracefile stopping inside a
        # record, and reading it would report a coverage over part of the tree.
        truncated = "SF:app.py\nDA:1,1\nDA:2,1\n"
        with tempfile.TemporaryDirectory() as temp:
            case = self._case(Path(temp), writes_report(truncated, into=INTO))

            result = case.run()

            self.assertEqual("REJECTED", result["status"])
            [reason] = result["decision"]["reasons"]
            self.assertIn("sensor error: gate full", reason)
            self.assertIn("did not finish writing it", reason)
            self.assertIsNone(result["worktree"])


if __name__ == "__main__":
    unittest.main()
