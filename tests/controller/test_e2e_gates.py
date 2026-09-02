"""A gate that fails, and a gate that changed what it was measuring."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from e2e_support import MUTATING_SENSOR
from harness import build_case, constitution


@unittest.skipUnless(
    sys.platform == "darwin",
    "controller confinement requires macOS sandbox-exec",
)
class GateFailureE2ETests(unittest.TestCase):
    def test_a_red_gate_stops_the_run_before_any_observation(self) -> None:
        stale = "grep -q 'return 0' app.py"
        for phase, constitution_text in (
            ("quick", constitution(quick_command=stale)),
            ("full", constitution(full_command=stale)),
        ):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temp:
                case = build_case(
                    Path(temp),
                    implementer="implement(ACCEPTABLE)",
                    constitution_text=constitution_text,
                )

                result = case.run()

                self.assertEqual("REJECTED", result["status"])
                self.assertNotIn("review", result)
                evidence = json.loads(
                    Path(result["run_dir"], "evidence.json").read_text(encoding="utf-8")
                )
                self.assertNotIn("review", evidence)
                self.assertFalse(Path(result["run_dir"], "review").exists())

    def test_a_quick_gate_that_changed_the_candidate_ends_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                # The external sensor is the one gate the source repository
                # is never measured with, so the only tree it can move is the
                # candidate.
                constitution_text=constitution(sensor_command=MUTATING_SENSOR),
            )

            result = case.run()

            self.assertEqual("REJECTED", result["status"])
            # A control failure and not a failing gate: the decision says the
            # tree changed, and never that a gate returned something.
            self.assertEqual(
                ["quick gates changed the candidate workspace"],
                result["decision"]["reasons"],
            )
            iteration = result["iterations"][-1]
            self.assertTrue(all(g["passed"] for g in iteration["quick_gates"]))
            self.assertEqual(
                [0] * len(iteration["quick_gates"]),
                [g["exit_code"] for g in iteration["quick_gates"]],
            )
            self.assertTrue(iteration["scope"]["passed"])
            self.assertNotEqual(
                iteration["actuator_state"]["sha256"],
                iteration["observed_state"]["sha256"],
            )
            self.assertTrue(Path(result["worktree"], "mutant.py").is_file())
            self.assertNotIn("full_gates", result)
            self.assertNotIn("review", result)

    def test_a_full_gate_that_changed_the_candidate_ends_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                constitution_text=constitution(
                    sensor_command=MUTATING_SENSOR, sensor_phase="full"
                ),
            )

            result = case.run()

            self.assertEqual("REJECTED", result["status"])
            self.assertEqual(
                ["full gates changed the candidate workspace"],
                result["decision"]["reasons"],
            )
            self.assertTrue(all(g["passed"] for g in result["full_gates"]))
            self.assertEqual(
                [0] * len(result["full_gates"]),
                [g["exit_code"] for g in result["full_gates"]],
            )
            iteration = result["iterations"][-1]
            self.assertTrue(all(g["passed"] for g in iteration["quick_gates"]))
            self.assertTrue(iteration["scope"]["passed"])
            # The state the quick phase left, against the state the full gates
            # were measuring when they finished.
            self.assertNotEqual(
                iteration["observed_state"]["sha256"],
                result["full_gate_state"]["sha256"],
            )
            self.assertTrue(Path(result["worktree"], "mutant.py").is_file())
            self.assertNotIn("review", result)


if __name__ == "__main__":
    unittest.main()
