import tempfile
import unittest
from pathlib import Path

from codeservo.evidence import (
    relative_evidence_paths,
    sha256_file,
    sha256_json,
    sha256_record,
)


class EvidenceTests(unittest.TestCase):
    def test_relativizes_recorded_paths_without_changing_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            run_dir = root / "runs" / "repo" / "run"
            payload = {
                "run_dir": str(run_dir),
                "worktree": str(root / "worktrees" / "repo" / "run"),
                "artifact": {"path": str(run_dir / "agent" / "events.jsonl")},
                "feedback": f"failure in {run_dir}",
            }

            portable = relative_evidence_paths(payload, run_dir)

            self.assertEqual(".", portable["run_dir"])
            self.assertEqual("../../../worktrees/repo/run", portable["worktree"])
            self.assertEqual("agent/events.jsonl", portable["artifact"]["path"])
            self.assertEqual(payload["feedback"], portable["feedback"])

    def test_hashes_file_bytes_and_canonical_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "output.log"
            path.write_text("result\n", encoding="utf-8")

            self.assertEqual(64, len(sha256_file(path)))
            self.assertEqual(
                sha256_json({"a": 1, "b": 2}),
                sha256_json({"b": 2, "a": 1}),
            )
            self.assertEqual(
                sha256_record({"status": "passed", "stdout_path": "/first"}),
                sha256_record({"status": "passed", "stdout_path": "/moved"}),
            )


if __name__ == "__main__":
    unittest.main()
