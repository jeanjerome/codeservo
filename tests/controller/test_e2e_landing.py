"""An accepted run entering the repository it measured, and saying so."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from codeservo.controller.landing import LandingError, land
from codeservo.evidence.journal import JOURNAL_NAME, LANDED_EVENT, read_journal
from codeservo.evidence.register import COLUMNS
from codeservo.evidence.verify import verify_run
from e2e_support import LOCATING_REVIEWER
from harness import build_case, commit_repository

ADDS_A_FILE = """
(worktree / "NOTES.md").write_text("landed with the change\\n")
implement(ACCEPTABLE)
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True
    ).stdout.strip()


@unittest.skipUnless(
    sys.platform == "darwin",
    "controller confinement requires macOS sandbox-exec",
)
class LandingE2ETests(unittest.TestCase):
    def test_an_accepted_run_lands_as_one_commit_on_its_base(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp), implementer="implement(ACCEPTABLE)", reviewer=LOCATING_REVIEWER
            )
            result = case.run()
            self.assertEqual("ACCEPTED", result["status"])
            run_dir = Path(result["run_dir"])
            base = result["base_commit"]

            landed = land(run_dir)

            # One commit, on the base the run measured, carrying the change.
            self.assertEqual(landed.commit, _git(case.repo, "rev-parse", "HEAD"))
            self.assertEqual(base, _git(case.repo, "rev-parse", "HEAD^"))
            self.assertEqual("", _git(case.repo, "status", "--porcelain"))
            self.assertEqual(
                "def value():\n    return 2\n",
                (case.repo / "app.py").read_text(encoding="utf-8"),
            )
            message = _git(case.repo, "log", "-1", "--format=%B")
            self.assertTrue(message.startswith(f"codeservo: land run {result['run_id']}"))
            self.assertIn(f"Base: {base}", message)
            self.assertIn(f"Patch-SHA256: {result['patch_sha256']}", message)
            # The journal says so, after the decision, and still verifies.
            events = read_journal(run_dir / JOURNAL_NAME)
            self.assertEqual("run.finished", events[-2]["type"])
            self.assertEqual(LANDED_EVENT, events[-1]["type"])
            self.assertEqual(
                {
                    "commit": landed.commit,
                    "base_commit": base,
                    "patch_sha256": result["patch_sha256"],
                },
                events[-1]["payload"],
            )
            report = verify_run(run_dir)
            self.assertEqual("VALID", report["status"])
            landing = [c for c in report["checks"] if c["name"] == "journal.landing"][0]
            self.assertEqual(f"landed as {landed.commit}", landing["detail"])
            # The record was not touched.
            evidence = json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
            self.assertEqual("ACCEPTED", evidence["status"])
            self.assertEqual(len(events) - 1, evidence["events"]["count"])
            # The review's one finding entered the register, uncovered.
            self.assertEqual(1, landed.findings)
            self.assertEqual(
                (case.state_dir / "findings" / "repo.tsv").resolve(), landed.register
            )
            lines = landed.register.read_text(encoding="utf-8").splitlines()
            self.assertEqual("\t".join(COLUMNS), lines[0])
            row = dict(zip(COLUMNS, lines[1].split("\t"), strict=True))
            self.assertEqual(result["run_id"], row["run_id"])
            self.assertEqual(landed.commit, row["commit"])
            self.assertEqual("minor", row["severity"])
            self.assertEqual("a note about the candidate", row["message"])
            self.assertEqual("none", row["covered_by"])

    def test_a_landed_run_is_not_landed_twice(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(ACCEPTABLE)")
            result = case.run()
            run_dir = Path(result["run_dir"])
            landed = land(run_dir)

            with self.assertRaisesRegex(LandingError, f"already landed as {landed.commit}"):
                land(run_dir)

            self.assertEqual(landed.commit, _git(case.repo, "rev-parse", "HEAD"))
            self.assertEqual("VALID", verify_run(run_dir)["status"])

    def test_a_run_that_was_not_accepted_is_not_landed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(UNACCEPTABLE)")
            result = case.run(max_iterations=1)
            self.assertEqual("REJECTED", result["status"])
            before = _git(case.repo, "rev-parse", "HEAD")

            with self.assertRaisesRegex(LandingError, "only an accepted run is landed"):
                land(Path(result["run_dir"]))

            self.assertEqual(before, _git(case.repo, "rev-parse", "HEAD"))

    def test_a_repository_that_moved_since_the_run_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(ACCEPTABLE)")
            result = case.run()
            (case.repo / "README.md").write_text("moved on\n", encoding="utf-8")
            commit_repository(case.repo, "moved on")
            moved = _git(case.repo, "rev-parse", "HEAD")

            with self.assertRaisesRegex(LandingError, "the repository moved since the run"):
                land(Path(result["run_dir"]))

            self.assertEqual(moved, _git(case.repo, "rev-parse", "HEAD"))
            self.assertEqual("", _git(case.repo, "status", "--porcelain"))
            events = read_journal(Path(result["run_dir"], JOURNAL_NAME))
            self.assertEqual("run.finished", events[-1]["type"])

    def test_a_repository_holding_uncommitted_work_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(ACCEPTABLE)")
            result = case.run()
            (case.repo / "scratch.txt").write_text("work in progress\n", encoding="utf-8")

            with self.assertRaisesRegex(LandingError, "uncommitted work"):
                land(Path(result["run_dir"]))

            self.assertEqual(result["base_commit"], _git(case.repo, "rev-parse", "HEAD"))

    def test_a_change_adding_a_file_lands_with_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer=ADDS_A_FILE)
            result = case.run()
            self.assertEqual("ACCEPTED", result["status"])

            landed = land(Path(result["run_dir"]), message="feat: value returns two")

            self.assertEqual(landed.commit, _git(case.repo, "rev-parse", "HEAD"))
            self.assertEqual(
                "landed with the change\n",
                (case.repo / "NOTES.md").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "feat: value returns two", _git(case.repo, "log", "-1", "--format=%s")
            )
            self.assertEqual(0, landed.findings)
            self.assertFalse(landed.register.exists())


if __name__ == "__main__":
    unittest.main()
