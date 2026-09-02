"""What `verify-run` must hold for every record, not only for written ones.

A record is the input this command exists to distrust. The cases beside these
move one field of a well-formed record and say what that does to the verdict;
these state what no record may obtain, however it is shaped.
"""

import json
import tempfile
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from codeservo.evidence.digests import sha256_file, sha256_path
from codeservo.evidence.verify import Verdict, VerificationError, verify_run
from properties import CLIMBS, climbing_locations, json_objects
from run_fixtures import build_run

CANARY = "canary.txt"
CANARY_TEXT = "a file the run does not hold\n"


def _outside(run_dir: Path) -> None:
    """The same file, at every level a generated location can climb to.

    Its content is identical wherever it sits, so a record naming any of them
    carries one digest, and the verdict cannot depend on which level was drawn.
    """
    ancestor = run_dir
    for _ in range(CLIMBS):
        ancestor = ancestor.parent
        (ancestor / CANARY).write_text(CANARY_TEXT, encoding="utf-8")


def _statuses(report: dict, name: str) -> list[str]:
    return [check["status"] for check in report["checks"] if check["name"] == name]


class ConfinementProperties(unittest.TestCase):
    """No record obtains a reading of a file outside the directory it names.

    The run directory is what the command says is its only intake. A record
    naming a file above it, or naming one absolutely, names a file this run
    does not hold, whatever that file turns out to be — so the check may not
    confirm it, and confirming it is the only way to tell that it was read.
    """

    @given(
        location=st.one_of(climbing_locations(CANARY), st.none()),
        absolute=st.booleans(),
    )
    def test_a_sensor_outside_the_run_is_never_confirmed(self, location, absolute):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = build_run(Path(tmp) / "a" / "b")
            _outside(run_dir)
            target = run_dir.parent / CANARY
            named = str(target) if absolute or location is None else location

            record = json.loads((run_dir / "evidence.json").read_text("utf-8"))
            record["sensors"]["task-outcome"]["path"] = named
            record["sensors"]["task-outcome"]["sha256"] = sha256_path(target)
            (run_dir / "evidence.json").write_text(json.dumps(record), "utf-8")

            report = verify_run(run_dir)
            self.assertNotIn("ok", _statuses(report, "sensor.task-outcome"))
            self.assertEqual(report["status"], Verdict.INVALID)

    @given(
        location=st.one_of(climbing_locations(CANARY), st.none()),
        absolute=st.booleans(),
    )
    def test_an_artefact_outside_the_run_is_never_confirmed(self, location, absolute):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = build_run(Path(tmp) / "a" / "b")
            _outside(run_dir)
            target = run_dir.parent / CANARY
            named = str(target) if absolute or location is None else location

            record = json.loads((run_dir / "evidence.json").read_text("utf-8"))
            gate = record["baseline"][0]
            gate["stdout_path"] = named
            gate["stdout_sha256"] = sha256_file(target)
            (run_dir / "evidence.json").write_text(json.dumps(record), "utf-8")

            report = verify_run(run_dir)
            self.assertNotIn("ok", _statuses(report, f"artifact.{named}"))

    @given(location=climbing_locations("events.jsonl"))
    def test_a_journal_outside_the_run_is_never_read(self, location):
        """The journal placed outside is the run's own, so it would verify.

        A malformed one outside would fail on its shape and say nothing about
        confinement. This one is genuine, so accepting it is exactly the
        statement that the file was reached.
        """
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = build_run(Path(tmp) / "a" / "b")
            genuine = (run_dir / "events.jsonl").read_text(encoding="utf-8")
            ancestor = run_dir
            for _ in range(CLIMBS):
                ancestor = ancestor.parent
                (ancestor / "events.jsonl").write_text(genuine, encoding="utf-8")

            record = json.loads((run_dir / "evidence.json").read_text("utf-8"))
            record["events"]["path"] = location
            (run_dir / "evidence.json").write_text(json.dumps(record), "utf-8")

            report = verify_run(run_dir)
            self.assertEqual(report["status"], Verdict.INVALID)


class TotalityProperties(unittest.TestCase):
    """Every record reaches a verdict or a refusal, and nothing else.

    `verify-run` answers `VALID`, `INVALID` or `INCOMPLETE`, or refuses the
    directory outright. An interpreter traceback is none of those, and a
    falsified record is exactly the input that would produce one.
    """

    @given(record=json_objects())
    def test_any_object_reaches_a_verdict_or_a_refusal(self, record):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = build_run(Path(tmp))
            (run_dir / "evidence.json").write_text(json.dumps(record), "utf-8")
            try:
                report = verify_run(run_dir)
            except VerificationError:
                return
            self.assertIn(report["status"], set(Verdict))

    @given(text=st.text(max_size=64))
    def test_any_text_reaches_a_verdict_or_a_refusal(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = build_run(Path(tmp))
            (run_dir / "evidence.json").write_text(text, encoding="utf-8")
            try:
                report = verify_run(run_dir)
            except VerificationError:
                return
            self.assertIn(report["status"], set(Verdict))


if __name__ == "__main__":
    unittest.main()
