"""The trajectory a run records as it happens."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from codeservo.evidence.digests import sha256_file, sha256_json
from codeservo.evidence.journal import JOURNAL_NAME, read_journal
from codeservo.evidence.verify import verify_run
from e2e_support import CONVERGING_IMPLEMENTER, JOURNAL_PROBE, LOCATING_REVIEWER
from harness import PIXI_TASK, build_case, commit_repository, constitution


@unittest.skipUnless(
    sys.platform == "darwin",
    "controller confinement requires macOS sandbox-exec",
)
class RunJournalE2ETests(unittest.TestCase):
    def journal(self, result: dict) -> list[dict]:
        return read_journal(Path(result["run_dir"], JOURNAL_NAME))

    def evidence(self, result: dict) -> dict:
        return json.loads(
            Path(result["run_dir"], "evidence.json").read_text(encoding="utf-8")
        )

    def test_the_journal_covers_the_trajectory_in_the_order_it_happened(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer=CONVERGING_IMPLEMENTER)

            result = case.run()

            self.assertEqual("ACCEPTED", result["status"])
            recorded = [event["type"] for event in self.journal(result)]
            self.assertEqual("run.started", recorded[0])
            self.assertEqual("run.finished", recorded[-1])
            self.assertEqual("decision.recorded", recorded[-2])
            self.assertLessEqual(
                {
                    "run.started",
                    "inputs.frozen",
                    "inference.profiles_frozen",
                    "baseline.finished",
                    "workspace.ready",
                    "actuator.started",
                    "actuator.finished",
                    "actuator.profile_observed",
                    "gate.finished",
                    "feedback.emitted",
                    "review.finished",
                    "review.profile_observed",
                    "decision.recorded",
                    "run.finished",
                },
                set(recorded),
            )
            # A run declaring no execution provider takes neither transition.
            self.assertNotIn("environment.validated", recorded)
            self.assertNotIn("environment.prepared", recorded)
            self.assertNotIn("budget.exhausted", recorded)
            self.assertLess(
                recorded.index("inputs.frozen"), recorded.index("baseline.finished")
            )
            self.assertLess(
                recorded.index("baseline.finished"), recorded.index("workspace.ready")
            )
            self.assertLess(
                recorded.index("workspace.ready"), recorded.index("actuator.started")
            )
            self.assertLess(
                recorded.index("feedback.emitted"), recorded.index("review.finished")
            )

    def test_one_gate_event_per_gate_result_the_record_holds(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer=CONVERGING_IMPLEMENTER)

            result = case.run()

            measured = [
                ("baseline", gate) for gate in result["baseline"]
            ]
            for iteration in result["iterations"]:
                measured += [("quick", gate) for gate in iteration["quick_gates"]]
                measured += [("full", gate) for gate in iteration.get("full_gates", [])]
            recorded = [
                event["payload"]
                for event in self.journal(result)
                if event["type"] == "gate.finished"
            ]

            self.assertEqual(len(measured), len(recorded))
            self.assertEqual(
                [
                    {
                        "phase": phase,
                        "name": gate["name"],
                        "passed": gate["passed"],
                        "result_sha256": gate["result_sha256"],
                    }
                    for phase, gate in measured
                ],
                recorded,
            )

    def test_the_events_block_describes_the_complete_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(ACCEPTABLE)")

            result = case.run()

            evidence = self.evidence(result)
            events = self.journal(result)
            block = evidence["events"]
            self.assertEqual(18, evidence["schema_version"])
            self.assertEqual(
                {"path", "count", "head_sha256", "file_sha256"}, set(block)
            )
            self.assertEqual(JOURNAL_NAME, block["path"])
            self.assertEqual(len(events), block["count"])
            self.assertEqual(events[-1]["sha256"], block["head_sha256"])
            self.assertEqual(
                sha256_file(Path(result["run_dir"], JOURNAL_NAME)),
                block["file_sha256"],
            )

    def test_the_decision_is_an_event_before_it_is_a_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                reviewer='emit_review({"criteria": [], "findings": []})',
            )

            result = case.run()

            self.assertEqual("REJECTED", result["status"])
            events = self.journal(result)
            self.assertEqual("decision.recorded", events[-2]["type"])
            self.assertEqual(
                {"status": "REJECTED", "reasons": result["decision"]["reasons"]},
                events[-2]["payload"],
            )
            self.assertEqual({"status": "REJECTED"}, events[-1]["payload"])

    def test_a_run_rejected_at_the_baseline_still_closes_its_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                constitution_text=constitution(quick_command="false"),
            )

            result = case.run()

            self.assertEqual("REJECTED", result["status"])
            self.assertIsNone(result["worktree"])
            recorded = [event["type"] for event in self.journal(result)]
            self.assertEqual(
                [
                    "run.started",
                    "inputs.frozen",
                    "inference.profiles_frozen",
                    "gate.finished",
                    "gate.finished",
                    "baseline.finished",
                    "decision.recorded",
                    "run.finished",
                ],
                recorded,
            )
            self.assertEqual("VALID", verify_run(Path(result["run_dir"]))["status"])

    def test_a_run_that_never_converges_records_the_budget_that_ended_it(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(UNACCEPTABLE)")

            result = case.run(max_iterations=2)

            self.assertEqual("REJECTED", result["status"])
            recorded = [event["type"] for event in self.journal(result)]
            self.assertEqual(
                ["budget.exhausted", "decision.recorded", "run.finished"],
                recorded[-3:],
            )

    def test_a_run_declaring_a_provider_records_both_environment_transitions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                provider=True,
                constitution_text=constitution(
                    execution="default", quick_task=PIXI_TASK
                ),
            )

            result = case.run()

            self.assertEqual("ACCEPTED", result["status"])
            recorded = [event["type"] for event in self.journal(result)]
            self.assertLess(
                recorded.index("environment.validated"),
                recorded.index("baseline.finished"),
            )
            self.assertLess(
                recorded.index("workspace.ready"),
                recorded.index("environment.prepared"),
            )
            self.assertLess(
                recorded.index("environment.prepared"),
                recorded.index("actuator.started"),
            )
            # The two declared files belong to the source repository, so a
            # verification reading the run directory alone still passes.
            self.assertEqual("VALID", verify_run(Path(result["run_dir"]))["status"])

    def test_a_gate_reads_every_transition_that_preceded_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                constitution_text=constitution(
                    sensor_command=f"{sys.executable} journal_probe.py"
                ),
            )
            (case.repo / "journal_probe.py").write_text(
                JOURNAL_PROBE, encoding="utf-8"
            )
            commit_repository(case.repo, "journal probe")

            result = case.run()

            self.assertEqual("ACCEPTED", result["status"])
            observed = Path(
                result["run_dir"],
                "iterations/01/quick/task-outcome.stdout.log",
            ).read_text(encoding="utf-8")
            for transition in (
                "run.started",
                "inputs.frozen",
                "baseline.finished",
                "workspace.ready",
                "actuator.finished",
            ):
                self.assertIn(transition, observed)

    def test_the_reviewer_result_is_recorded_as_the_reviewer_produced_it(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                reviewer=LOCATING_REVIEWER,
            )

            result = case.run()

            self.assertEqual("ACCEPTED", result["status"])
            evidence = self.evidence(result)
            review = evidence["iterations"][-1]["review"]
            located = review["result"]["findings"][0]["path"]
            self.assertTrue(Path(located).is_absolute())
            self.assertEqual(str(Path(result["worktree"], "app.py")), located)
            # The digest was taken over what the reviewer returned, so it
            # recomputes from the document the run left behind.
            self.assertEqual(sha256_json(review["result"]), review["result_sha256"])
            self.assertEqual("VALID", verify_run(Path(result["run_dir"]))["status"])

    def test_a_verified_run_is_valid_and_a_moved_digest_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer=CONVERGING_IMPLEMENTER)

            result = case.run()
            run_dir = Path(result["run_dir"])
            report = verify_run(run_dir)

            self.assertEqual("VALID", report["status"])
            self.assertEqual([], report["failures"])
            self.assertEqual([], report["missing"])

            record = json.loads(
                (run_dir / "evidence.json").read_text(encoding="utf-8")
            )
            record["status"] = "REJECTED"
            (run_dir / "evidence.json").write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            edited = verify_run(run_dir)

            self.assertEqual("INVALID", edited["status"])
            self.assertTrue(
                all(JOURNAL_NAME in failure for failure in edited["failures"])
            )


if __name__ == "__main__":
    unittest.main()
