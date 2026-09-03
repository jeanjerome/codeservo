"""The structured document a gate writes beside its exit code."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from codeservo.evidence.digests import sha256_file
from codeservo.evidence.verify import verify_run
from e2e_support import OBSERVATION, toml_basic, writes_observation
from harness import build_case, constitution


@unittest.skipUnless(
    sys.platform == "darwin",
    "external sensor isolation requires macOS sandbox-exec",
)
class GateObservationE2ETests(unittest.TestCase):
    """A gate's second, structured answer, from the constitution to the record."""

    def _evidence(self, result: dict) -> dict:
        return json.loads(
            Path(result["run_dir"], "evidence.json").read_text(encoding="utf-8")
        )

    def test_the_record_carries_the_document_the_gate_wrote(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                constitution_text=constitution(
                    sensor_command=writes_observation(OBSERVATION),
                    sensor_result_format="codeservo-json",
                ),
            )

            result = case.run()

            self.assertEqual("ACCEPTED", result["status"])
            evidence = self._evidence(result)
            quick = {g["name"]: g for g in evidence["iterations"][-1]["quick_gates"]}
            sensor = quick["task-outcome"]

            # Every gate names the format it answered with; only the one that
            # declared a document carries the four fields describing it.
            self.assertEqual("exit-code", quick["syntax"]["result_format"])
            self.assertEqual("codeservo-json", sensor["result_format"])
            self.assertEqual("valid", sensor["observation_status"])
            self.assertIsNone(sensor["observation_error"])
            self.assertEqual(
                "iterations/01/quick/task-outcome.observation.json",
                sensor["observation_path"],
            )
            # Kept beside that gate's logs, and byte for byte as it was written.
            kept = Path(result["run_dir"], sensor["observation_path"])
            self.assertEqual(
                json.dumps(OBSERVATION, sort_keys=True),
                kept.read_text(encoding="utf-8"),
            )
            self.assertEqual(sha256_file(kept), sensor["observation_sha256"])
            self.assertEqual(
                "iterations/01/quick", str(Path(sensor["stdout_path"]).parent)
            )

    def test_a_run_holding_an_observation_verifies_and_a_moved_one_does_not(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                constitution_text=constitution(
                    sensor_command=writes_observation(OBSERVATION),
                    sensor_result_format="codeservo-json",
                ),
            )

            result = case.run()
            run_dir = Path(result["run_dir"])

            self.assertEqual("VALID", verify_run(run_dir)["status"])
            kept = run_dir / "iterations" / "01" / "quick" / "task-outcome.observation.json"
            kept.write_text(
                json.dumps({**OBSERVATION, "summary": "rewritten"}), encoding="utf-8"
            )

            report = verify_run(run_dir)

            self.assertEqual("INVALID", report["status"])
            self.assertTrue(
                any(
                    "task-outcome.observation.json" in failure
                    for failure in report["failures"]
                ),
                report["failures"],
            )

    def test_a_sensor_fault_ends_the_run_in_every_phase(self) -> None:
        # A document breaking the contract, from a gate that also exits
        # non-zero: the decision says the sensor is broken and never that a
        # gate failed.
        broken = writes_observation(
            {**OBSERVATION, "status": "errored"}, exit_code=1
        )
        for phase in ("quick", "full"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temp:
                case = build_case(
                    Path(temp),
                    implementer="implement(ACCEPTABLE)",
                    constitution_text=constitution(
                        sensor_command=broken,
                        sensor_phase=phase,
                        sensor_result_format="codeservo-json",
                    ),
                )

                result = case.run()

                self.assertEqual("REJECTED", result["status"])
                reasons = result["decision"]["reasons"]
                self.assertEqual(1, len(reasons), reasons)
                self.assertTrue(
                    reasons[0].startswith("sensor error: gate task-outcome:"),
                    reasons[0],
                )
                self.assertIn("field status", reasons[0])
                self.assertNotIn("gate failed", " ".join(reasons))
                # One iteration, no feedback, and no reviewer.
                self.assertEqual(1, len(result["iterations"]))
                self.assertIsNone(
                    result["iterations"][-1].get("controller_feedback")
                )
                self.assertFalse(
                    Path(
                        result["run_dir"], "iterations", "01", "controller-feedback.md"
                    ).exists()
                )
                self.assertNotIn("review", result)
                # The document the controller refused is kept all the same.
                gates = (
                    result["full_gates"]
                    if phase == "full"
                    else result["iterations"][-1]["quick_gates"]
                )
                faulty = [g for g in gates if g["name"] == "task-outcome"][0]
                self.assertEqual("invalid", faulty["observation_status"])
                self.assertEqual(1, faulty["exit_code"])
                self.assertTrue(Path(faulty["observation_path"]).is_file())

    def test_a_baseline_sensor_fault_ends_the_run_before_any_checkout(self) -> None:
        broken = writes_observation(
            {**OBSERVATION, "metrics": {"checked": True}}, exit_code=1
        )
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                constitution_text=constitution(
                    quick_command=toml_basic(broken),
                    quick_result_format="codeservo-json",
                ),
            )

            result = case.run()

            self.assertEqual("REJECTED", result["status"])
            # The reason a baseline gate ends a run on, and that reason alone.
            self.assertEqual(1, len(result["decision"]["reasons"]))
            self.assertEqual(
                "sensor error: gate syntax: field metrics.checked must be a number",
                result["decision"]["reasons"][0],
            )
            self.assertIsNone(result["worktree"])
            self.assertEqual([], result["iterations"])
            baseline = {g["name"]: g for g in result["baseline"]}
            self.assertEqual("invalid", baseline["syntax"]["observation_status"])
            self.assertEqual(1, baseline["syntax"]["exit_code"])

    def test_a_gate_that_wrote_nothing_ends_the_run_as_a_sensor_fault(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                constitution_text=constitution(
                    sensor_command="grep -q \"return 2\" app.py",
                    sensor_result_format="codeservo-json",
                ),
            )

            result = case.run()

            self.assertEqual("REJECTED", result["status"])
            self.assertEqual(
                ["sensor error: gate task-outcome: the gate wrote no observation"],
                result["decision"]["reasons"],
            )
            sensor = [
                g
                for g in result["iterations"][-1]["quick_gates"]
                if g["name"] == "task-outcome"
            ][0]
            # The gate passed; it is the missing document that ends the run.
            self.assertTrue(sensor["passed"])
            self.assertEqual("absent", sensor["observation_status"])
            self.assertIsNone(sensor["observation_path"])

    def test_a_valid_document_reporting_a_failure_is_fed_back(self) -> None:
        failing = writes_observation(
            {
                **OBSERVATION,
                "status": "failed",
                "summary": "still returns 1",
                "findings": [
                    {
                        "id": "app.py::main",
                        "severity": "major",
                        "path": "app.py",
                        "line": 3,
                        "message": "returns 1 where 2 is required",
                    }
                ],
            },
            exit_code=1,
        )
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                constitution_text=constitution(
                    sensor_command=failing,
                    sensor_result_format="codeservo-json",
                ),
            )

            result = case.run()

            # The gate failed, the loop iterated on the feedback it always
            # writes, and the budget ended the run: never a sensor error.
            self.assertEqual("REJECTED", result["status"])
            self.assertEqual(
                ["quick gates did not converge within 2 iterations"],
                result["decision"]["reasons"],
            )
            self.assertEqual(2, len(result["iterations"]))
            for iteration in result["iterations"]:
                # The actuator is told what the gate's own document says,
                # before the tail of what the gate printed.
                text = iteration["controller_feedback"]["text"]
                self.assertIn("Gate task-outcome FAILED", text)
                self.assertIn("Summary: still returns 1", text)
                self.assertIn(
                    "- [major] app.py:3: returns 1 where 2 is required", text
                )
                self.assertIn("Metrics: checked=1 surviving=0", text)
                self.assertLess(text.index("Summary:"), text.index("stdout (tail):"))
                # The prompt names every criterion by its id, and from the
                # second iteration on recaps what each earlier one reached.
                prompt = Path(
                    result["run_dir"], iteration["prompt"]["path"]
                ).read_text(encoding="utf-8")
                self.assertIn("ACCEPTANCE CRITERIA\n===================\n- AC1:", prompt)
                if iteration["iteration"] == 1:
                    self.assertIn("None. This is the first iteration.", prompt)
                else:
                    self.assertIn(
                        "Iterations so far:\n- Iteration 1: scope OK; quick gates: ",
                        prompt,
                    )
                    self.assertIn("failed: task-outcome (still returns 1)", prompt)
                    self.assertIn("Feedback from the previous iteration:\nGate task-outcome FAILED", prompt)
                sensor = [
                    g
                    for g in iteration["quick_gates"]
                    if g["name"] == "task-outcome"
                ][0]
                self.assertEqual("valid", sensor["observation_status"])
                self.assertFalse(sensor["passed"])

    def test_a_constitution_declaring_nothing_behaves_as_before(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                # The gate reports what it was handed: nothing.
                constitution_text=constitution(
                    sensor_command=(
                        'test -z "$CODESERVO_OBSERVATION_PATH"'
                        " && grep -q \"return 2\" app.py"
                    )
                ),
            )

            result = case.run()

            self.assertEqual("ACCEPTED", result["status"])
            recorded = [
                *result["baseline"],
                *result["iterations"][-1]["quick_gates"],
                *result["full_gates"],
            ]
            for gate in recorded:
                with self.subTest(gate=gate["name"]):
                    self.assertEqual("exit-code", gate["result_format"])
                    for field in (
                        "observation_status",
                        "observation_error",
                        "observation_path",
                        "observation_sha256",
                    ):
                        self.assertNotIn(field, gate)
            self.assertEqual(
                [],
                sorted(Path(result["run_dir"]).rglob("*.observation.json")),
            )


if __name__ == "__main__":
    unittest.main()
