import json
import sys
import tempfile
import unittest
from pathlib import Path

from harness import build_case, commit_repository, constitution


@unittest.skipUnless(
    sys.platform == "darwin",
    "controller confinement requires macOS sandbox-exec",
)
class RejectionPathTests(unittest.TestCase):
    def assert_rejected(self, result: dict, reason: str) -> None:
        self.assertEqual("REJECTED", result["status"])
        self.assertTrue(
            any(reason in recorded for recorded in result["decision"]["reasons"]),
            f"{reason!r} not in {result['decision']['reasons']}",
        )
        evidence = json.loads(
            Path(result["run_dir"], "evidence.json").read_text(encoding="utf-8")
        )
        self.assertEqual("REJECTED", evidence["status"])
        self.assertEqual(result["decision"]["reasons"], evidence["decision"]["reasons"])

    def test_rejects_a_dirty_source_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(ACCEPTABLE)")
            (case.repo / "uncommitted.py").write_text("x = 1\n", encoding="utf-8")

            result = case.run()

            self.assert_rejected(result, "source repository is not clean")
            self.assertEqual([], result["iterations"])
            self.assertNotIn("baseline", result)

    def test_rejects_a_red_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                constitution_text=constitution(quick_command="false"),
            )

            result = case.run()

            self.assert_rejected(result, "baseline gate failed")
            self.assertFalse(result["baseline"][0]["passed"])
            self.assertEqual([], result["iterations"])

    def test_rejects_a_baseline_gate_that_mutates_the_source_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                constitution_text=constitution(quick_command="touch mutation.txt"),
            )

            result = case.run()

            self.assert_rejected(result, "baseline gate mutated the source repository")
            self.assertTrue(all(gate["passed"] for gate in result["baseline"]))
            self.assertEqual([], result["iterations"])

    def test_rejects_a_failing_actuator(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="raise SystemExit(3)")

            result = case.run()

            self.assert_rejected(result, "implementer exited with 3")
            self.assertEqual(1, len(result["iterations"]))
            self.assertEqual(3, result["iterations"][0]["agent"]["exit_code"])

    def test_rejects_an_actuator_that_outlives_its_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="time.sleep(30)")

            result = case.run(agent_timeout_seconds=1)

            self.assert_rejected(result, "implementer timed out after 1s")
            self.assertEqual(1, len(result["iterations"]))
            self.assertIn("agent_error", result["iterations"][0])
            self.assertNotIn("agent", result["iterations"][0])

    def test_rejects_an_actuator_that_never_satisfies_the_sensor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(UNACCEPTABLE)")

            result = case.run(max_iterations=2)

            self.assert_rejected(result, "quick gates did not converge within 2")
            self.assertEqual(2, len(result["iterations"]))
            first, second = result["iterations"]
            self.assertFalse(first["quick_gates"][1]["passed"])
            self.assertIn(
                "Gate task-outcome FAILED", first["controller_feedback"]["text"]
            )
            self.assertEqual(
                first["controller_feedback"]["text"], second["feedback_received"]
            )

    def test_rejects_a_change_that_keeps_violating_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="""
                (worktree / ".codeservo" / "extra.toml").write_text("owned = false\\n")
                implement(ACCEPTABLE)
                """,
            )

            result = case.run(max_iterations=2)

            self.assert_rejected(result, "quick gates did not converge within 2")
            iteration = result["iterations"][0]
            self.assertFalse(iteration["scope"]["passed"])
            self.assertIn("protected path changed", iteration["scope"]["summary"])
            self.assertTrue(all(gate["passed"] for gate in iteration["quick_gates"]))
            self.assertIn(
                "Structural invariant failures",
                iteration["controller_feedback"]["text"],
            )

    def test_rejects_a_red_full_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                constitution_text=constitution(
                    full_command="grep -q 'return 0' app.py"
                ),
            )

            result = case.run()

            self.assert_rejected(result, "full gate failed")
            self.assertEqual(1, len(result["iterations"]))
            self.assertFalse(result["full_gates"][0]["passed"])
            self.assertNotIn("review", result)

    def test_rejects_a_failing_reviewer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                reviewer="raise SystemExit(4)",
            )

            result = case.run()

            self.assert_rejected(result, "reviewer exited with 4")
            self.assertTrue(all(gate["passed"] for gate in result["full_gates"]))

    def test_rejects_a_reviewer_that_answers_outside_the_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                reviewer='print("the change looks fine to me")',
            )

            result = case.run()

            self.assert_rejected(result, "invalid reviewer output")

    def test_rejects_a_blocking_review_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                reviewer="""
                emit_review(
                    {
                        "criteria": SATISFIED["criteria"],
                        "findings": [
                            {
                                "severity": "major",
                                "path": "app.py",
                                "line": 2,
                                "message": "value() ignores its caller",
                                "evidence": "app.py:2",
                            }
                        ],
                    }
                )
                """,
            )

            result = case.run()

            self.assert_rejected(result, "major finding: value() ignores its caller")
            self.assertTrue(all(gate["passed"] for gate in result["full_gates"]))

    def test_rejects_a_review_that_skips_an_acceptance_criterion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                reviewer='emit_review({"criteria": [], "findings": []})',
            )

            result = case.run()

            self.assert_rejected(result, "review missing criterion AC1")

    def test_records_the_candidate_change_of_a_rejected_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                reviewer="raise SystemExit(4)",
            )

            result = case.run()

            patch_path = Path(result["run_dir"], "change.patch")
            self.assertTrue(patch_path.is_file())
            self.assertIn("return 2", patch_path.read_text(encoding="utf-8"))
            self.assertEqual(64, len(result["patch_sha256"]))


if __name__ == "__main__":
    unittest.main()
