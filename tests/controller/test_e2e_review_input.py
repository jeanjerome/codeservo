"""What the read-only reviewer is told, and what it is never told."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from codeservo.evidence.digests import sha256_text
from e2e_support import canonical
from harness import build_case, constitution


@unittest.skipUnless(
    sys.platform == "darwin",
    "controller confinement requires macOS sandbox-exec",
)
class ReviewObservationE2ETests(unittest.TestCase):
    def test_bounds_gate_observations_and_hides_controller_locations(self) -> None:
        chatty_sensor = (
            "for i in $(seq 1 300); do echo \"line $i\"; done; "
            "echo \"sensor at $CODESERVO_SENSOR_PATH\"; "
            "grep -q \"return 2\" app.py"
        )
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                constitution_text=constitution(sensor_command=chatty_sensor),
            )

            result = case.run()

            self.assertEqual("ACCEPTED", result["status"])
            review = result["iterations"][-1]["review"]
            prompt = Path(review["prompt"]["path"]).read_text(encoding="utf-8")
            observed = {
                gate["name"]: gate for gate in review["observations"]["gates"]
            }
            emitted = observed["task-outcome"]["stdout_tail"].splitlines()

            self.assertEqual(120, len(emitted))
            self.assertEqual("line 182", emitted[0])
            self.assertEqual("sensor at <redacted>/sensors/task-outcome", emitted[-1])
            self.assertNotIn(result["run_dir"], prompt)
            self.assertNotIn(result["worktree"], prompt)
            self.assertNotIn("line 181", prompt)

    def test_records_the_observations_before_the_reviewer_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                reviewer="raise SystemExit(4)",
            )

            result = case.run()

            self.assertEqual("REJECTED", result["status"])
            evidence = json.loads(
                Path(result["run_dir"], "evidence.json").read_text(encoding="utf-8")
            )
            review = evidence["iterations"][-1]["review"]
            # The reviewer failed after it was handed the bundle, which cannot
            # erase what it received.
            self.assertNotIn("result", review)
            self.assertEqual(64, len(review["prompt"]["sha256"]))
            self.assertEqual(
                ["syntax", "task-outcome", "full"],
                [gate["name"] for gate in review["observations"]["gates"]],
            )
            self.assertEqual(
                sha256_text(canonical(review["observations"])),
                review["observations_sha256"],
            )


if __name__ == "__main__":
    unittest.main()
