import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeservo.evidence import digests
from codeservo.evidence.digests import (
    VERBATIM_TRAILS,
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

    def test_leaves_a_document_recorded_as_its_producer_returned_it(self) -> None:
        # The reviewer's result is digested over what the reviewer returned, so
        # a location it names is part of that document and not of the record.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            run_dir = root / "runs" / "repo" / "run"
            located = str(root / "worktrees" / "repo" / "run" / "app.py")
            prompt = run_dir / "iterations" / "01" / "review" / "prompt.md"
            payload = {
                "iterations": [
                    {
                        "review": {
                            "prompt": {"path": str(prompt)},
                            "result": {
                                "findings": [{"path": located, "severity": "minor"}],
                            },
                        }
                    }
                ]
            }

            portable = relative_evidence_paths(payload, run_dir)
            review = portable["iterations"][0]["review"]

            self.assertEqual(located, review["result"]["findings"][0]["path"])
            self.assertEqual("iterations/01/review/prompt.md", review["prompt"]["path"])
            self.assertEqual(
                sha256_json(payload["iterations"][0]["review"]["result"]),
                sha256_json(review["result"]),
            )

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


class VerbatimContractTests(unittest.TestCase):
    """The one declaration of which documents are recorded as returned.

    The relativisation here and the verification beside it both read it, so
    what it holds, what shape it holds it in, and where it is reachable from
    are stated once rather than left to whichever reader is looked at.
    """

    def test_the_contract_is_a_frozen_set_of_trails(self) -> None:
        self.assertIsInstance(VERBATIM_TRAILS, frozenset)
        for trail in VERBATIM_TRAILS:
            self.assertIsInstance(trail, tuple)
            for step in trail:
                self.assertIsInstance(step, str)
        self.assertIn(("iterations", "review", "result"), VERBATIM_TRAILS)

    def test_the_writing_side_reaches_it_without_the_verification(self) -> None:
        """The declaration sits under the reader that writes a record.

        Stating it in the verification instead would make the module that
        writes a record load the module that audits one.
        """
        source = Path(digests.__file__).resolve().parents[2]
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, codeservo.evidence.digests as digests;"
                "print(('iterations', 'review', 'result') in digests.VERBATIM_TRAILS);"
                "print('codeservo.evidence.verify' in sys.modules)",
            ],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONPATH": str(source)},
        )
        self.assertEqual(["True", "False"], completed.stdout.split()[:2])

    def test_relativisation_leaves_every_declared_trail_and_no_other(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            run_dir = root / "runs" / "repo" / "run"
            located = str(root / "worktrees" / "repo" / "run" / "app.py")
            payload = {
                "returned": {"findings": [{"path": located}]},
                "artifact": {"path": str(run_dir / "agent" / "events.jsonl")},
            }

            # A trail nobody declares is relativised like any other.
            portable = relative_evidence_paths(payload, run_dir)
            self.assertEqual(
                "../../../worktrees/repo/run/app.py",
                portable["returned"]["findings"][0]["path"],
            )

            declared = VERBATIM_TRAILS | {("returned",)}
            with patch.object(digests, "VERBATIM_TRAILS", declared):
                portable = relative_evidence_paths(payload, run_dir)

            self.assertEqual(located, portable["returned"]["findings"][0]["path"])
            self.assertEqual("agent/events.jsonl", portable["artifact"]["path"])


if __name__ == "__main__":
    unittest.main()
