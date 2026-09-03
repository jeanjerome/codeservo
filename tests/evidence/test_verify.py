"""Deciding about a run directory from that directory alone."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeservo.evidence import digests, verify
from codeservo.evidence.digests import sha256_json
from codeservo.evidence.journal import JOURNAL_NAME
from codeservo.evidence.verify import (
    JOURNAL_EVIDENCE_VERSION,
    REPORT_SCHEMA_VERSION,
    Verdict,
    VerificationError,
    render_report,
    verify_run,
)
from run_fixtures import (
    RUN_ID,
    build_run,
    journal_lines,
    named,
    read_record,
    rewrite_journal,
    rewrite_record,
)


class ValidRunTests(unittest.TestCase):
    def test_a_run_whose_every_proof_holds_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))

            report = verify_run(run_dir)

            self.assertEqual("VALID", report["status"])
            self.assertEqual([], report["failures"])
            self.assertEqual([], report["missing"])

    def test_the_report_carries_the_six_recorded_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))

            report = verify_run(run_dir)

            self.assertEqual(
                {
                    "schema_version",
                    "run_id",
                    "status",
                    "checks",
                    "failures",
                    "missing",
                },
                set(report),
            )
            self.assertEqual(REPORT_SCHEMA_VERSION, report["schema_version"])
            self.assertEqual(RUN_ID, report["run_id"])
            self.assertTrue(report["checks"])
            for check in report["checks"]:
                self.assertEqual({"name", "status", "detail"}, set(check))

    def test_names_every_relation_the_contract_requires(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))

            names = {check["name"] for check in verify_run(run_dir)["checks"]}

            self.assertLessEqual(
                {
                    "input.TASK.md",
                    "input.constitution.toml",
                    "sensor.task-outcome",
                    "artifact.environment/packages.json",
                    "artifact.iterations/01/prompt.md",
                    "artifact.iterations/01/agent/events.jsonl",
                    "artifact.baseline/unit.stdout.log",
                    "artifact.iterations/01/review/result.json",
                    "artifact.change.patch",
                    "digest.baseline.0",
                    "digest.iterations.1.quick_gates.0",
                    "digest.iterations.1.full_gates.0",
                    "digest.iterations.1.agent",
                    "digest.iterations.1.review.result",
                    "digest.iterations.1.review.observations",
                    "digest.iterations.1.review.meta",
                    "journal.sequence",
                    "journal.chain",
                    "journal.digests",
                    "journal.events",
                    "journal.decision",
                },
                names,
            )

    def test_the_verification_creates_and_changes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))

            def snapshot() -> dict:
                return {
                    str(path.relative_to(run_dir)): path.read_bytes()
                    for path in sorted(run_dir.rglob("*"))
                    if path.is_file()
                }

            before = snapshot()
            report = verify_run(run_dir)

            self.assertEqual(before, snapshot())
            self.assertEqual("ACCEPTED", read_record(run_dir)["status"])
            self.assertEqual("VALID", report["status"])

    def test_the_human_listing_names_each_check_and_the_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))
            report = verify_run(run_dir)

            listing = render_report(report)

            for check in report["checks"]:
                self.assertIn(check["name"], listing)
            self.assertIn(RUN_ID, listing)
            self.assertIn("status: VALID", listing)


class NotVerifiableTests(unittest.TestCase):
    def test_the_declared_environment_files_belong_to_the_source_repository(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))

            report = verify_run(run_dir)

            for name, location in (
                ("environment.manifest_path", "pyproject.toml"),
                ("environment.lock_path", "pixi.lock"),
            ):
                check = named(report, name)
                self.assertEqual("not_verifiable", check["status"])
                self.assertIn(location, check["detail"])
            # Neither a failure nor a missing proof: the run stays valid.
            self.assertEqual("VALID", report["status"])

    def test_a_digest_without_a_companion_path_is_not_a_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))
            record = read_record(run_dir)
            record["environment"]["candidate"] = {"manifest_sha256": "0" * 64}
            rewrite_record(run_dir, record)

            report = verify_run(run_dir)

            self.assertEqual("VALID", report["status"])
            self.assertNotIn(
                "candidate", " ".join(check["name"] for check in report["checks"])
            )


class InvalidRunTests(unittest.TestCase):
    def _report(self, run_dir: Path) -> dict:
        report = verify_run(run_dir)
        self.assertEqual("INVALID", report["status"])
        return report

    def test_a_digest_that_disagrees_with_the_artefact_it_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))
            (run_dir / "baseline" / "unit.stdout.log").write_text(
                "rewritten\n", encoding="utf-8"
            )

            report = self._report(run_dir)

            self.assertEqual(
                ["baseline/unit.stdout.log"],
                [
                    failure.split(":")[0]
                    for failure in report["failures"]
                ],
            )

    def test_an_artefact_the_record_names_and_the_run_does_not_hold(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))
            (run_dir / "iterations" / "01" / "prompt.md").unlink()

            report = self._report(run_dir)

            self.assertEqual(1, len(report["failures"]))
            self.assertIn("iterations/01/prompt.md", report["failures"][0])
            self.assertEqual(
                "failed", named(report, "artifact.iterations/01/prompt.md")["status"]
            )

    def test_a_frozen_sensor_that_changed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))
            (run_dir / "sensors" / "task-outcome" / "README.md").write_text(
                "rewritten\n", encoding="utf-8"
            )

            report = self._report(run_dir)

            self.assertIn("sensors/task-outcome", report["failures"][0])

    def test_a_recomputed_gate_digest_that_no_longer_describes_the_record(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))
            record = read_record(run_dir)
            record["iterations"][0]["full_gates"][0]["passed"] = False
            rewrite_record(run_dir, record)

            report = self._report(run_dir)

            self.assertEqual(
                [
                    "iterations.1.full_gates.0.result_sha256:"
                    " does not describe what the record holds"
                ],
                report["failures"],
            )

    def test_a_reviewer_result_that_was_edited_after_the_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))
            record = read_record(run_dir)
            review = record["iterations"][0]["review"]
            review["result"]["findings"] = [{"severity": "blocker"}]
            rewrite_record(run_dir, record)

            report = self._report(run_dir)

            self.assertEqual(
                [
                    "iterations.1.review.result_sha256:"
                    " does not describe what the record holds"
                ],
                report["failures"],
            )

    def test_a_journal_whose_lines_were_reordered(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))
            lines = journal_lines(run_dir)
            lines[1], lines[2] = lines[2], lines[1]
            rewrite_journal(run_dir, lines)

            report = self._report(run_dir)

            self.assertTrue(
                all(JOURNAL_NAME in failure for failure in report["failures"])
            )
            self.assertEqual("failed", named(report, "journal.sequence")["status"])
            self.assertEqual("failed", named(report, "journal.chain")["status"])

    def test_a_journal_line_that_was_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))
            lines = journal_lines(run_dir)
            del lines[2]
            rewrite_journal(run_dir, lines)

            report = self._report(run_dir)

            self.assertEqual("failed", named(report, "journal.events")["status"])

    def test_a_journal_line_that_was_altered(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))
            lines = journal_lines(run_dir)
            event = json.loads(lines[0])
            event["payload"]["base_commit"] = "def"
            lines[0] = json.dumps(event, sort_keys=True, separators=(",", ":"))
            rewrite_journal(run_dir, lines)

            report = self._report(run_dir)

            self.assertEqual("failed", named(report, "journal.digests")["status"])

    def test_a_journal_a_finished_run_no_longer_holds(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))
            (run_dir / JOURNAL_NAME).unlink()

            report = self._report(run_dir)

            self.assertIn(JOURNAL_NAME, report["failures"][0])

    def test_a_status_edited_after_the_decision_was_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(
                Path(temp), status="REJECTED", reasons=("full gate failed",)
            )
            record = read_record(run_dir)
            record["status"] = "ACCEPTED"
            record["decision"] = {"reasons": []}
            rewrite_record(run_dir, record)

            report = self._report(run_dir)

            self.assertEqual("failed", named(report, "journal.decision")["status"])
            self.assertTrue(
                all(JOURNAL_NAME in failure for failure in report["failures"])
            )
            # The verification reports the disagreement; it never rewrites it.
            self.assertEqual("ACCEPTED", read_record(run_dir)["status"])

    def test_reasons_edited_after_the_decision_was_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(
                Path(temp), status="REJECTED", reasons=("full gate failed",)
            )
            record = read_record(run_dir)
            record["decision"] = {"reasons": ["a gentler reason"]}
            rewrite_record(run_dir, record)

            report = self._report(run_dir)

            self.assertIn(
                f"{JOURNAL_NAME}: decision.recorded states other reasons",
                report["failures"],
            )

    def test_an_events_block_that_no_longer_describes_the_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))
            record = read_record(run_dir)
            record["events"]["count"] = record["events"]["count"] + 1
            rewrite_record(run_dir, record)

            report = self._report(run_dir)

            self.assertIn(
                f"{JOURNAL_NAME}: the recorded events block counts",
                report["failures"],
            )


class IncompleteRunTests(unittest.TestCase):
    def test_a_record_that_predates_the_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(
                Path(temp), schema_version=JOURNAL_EVIDENCE_VERSION - 1, journal=False
            )

            report = verify_run(run_dir)

            self.assertEqual("INCOMPLETE", report["status"])
            self.assertEqual([], report["failures"])
            self.assertEqual(1, len(report["missing"]))
            self.assertIn(JOURNAL_NAME, report["missing"][0])
            self.assertEqual("missing", named(report, "journal")["status"])

    def test_a_run_that_never_finished(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp), status="RUNNING")

            report = verify_run(run_dir)

            self.assertEqual("INCOMPLETE", report["status"])
            self.assertEqual([], report["failures"])
            self.assertTrue(
                all(JOURNAL_NAME in absent for absent in report["missing"])
            )
            # The chain it did write is still read, and still holds.
            self.assertEqual("ok", named(report, "journal.chain")["status"])
            self.assertEqual("RUNNING", read_record(run_dir)["status"])

    def test_a_broken_artefact_of_an_unfinished_run_is_still_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp), status="RUNNING")
            (run_dir / "TASK.md").write_text("# Other\n", encoding="utf-8")

            report = verify_run(run_dir)

            self.assertEqual("INVALID", report["status"])
            self.assertIn("TASK.md", report["failures"][0])


class UnreadableRecordTests(unittest.TestCase):
    def test_a_directory_holding_no_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(VerificationError):
                verify_run(Path(temp))

    def test_a_record_that_is_not_readable_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "evidence.json").write_text("{oops", encoding="utf-8")

            with self.assertRaises(VerificationError):
                verify_run(Path(temp))

    def test_a_record_naming_something_else_where_gates_belong(self) -> None:
        """A verdict, not a traceback: the record is the input to distrust."""
        for field in ("baseline", "iterations"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp:
                run_dir = build_run(Path(temp))
                record = json.loads(
                    (run_dir / "evidence.json").read_text(encoding="utf-8")
                )
                record[field] = True
                (run_dir / "evidence.json").write_text(
                    json.dumps(record), encoding="utf-8"
                )

                report = verify_run(run_dir)

                self.assertIn(report["status"], set(Verdict))


class VerbatimContractTests(unittest.TestCase):
    """The verification honours the declaration the writing side carries.

    A document the two readers disagreed about would make the verification
    look inside the run directory for a location that belongs to the document,
    fail to find it, and answer INVALID on a sound record.
    """

    def test_the_verification_reads_the_one_declaration(self) -> None:
        self.assertIs(digests.VERBATIM_TRAILS, verify.VERBATIM_TRAILS)

    def test_a_pair_under_a_trail_nobody_declares_is_an_artefact_of_the_run(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))
            record = read_record(run_dir)
            record["returned"] = {"path": "../outside.log", "sha256": "0" * 64}
            rewrite_record(run_dir, record)

            report = verify_run(run_dir)

            self.assertEqual(
                ["../outside.log: the record names a path outside this run"],
                report["failures"],
            )
            self.assertEqual("INVALID", report["status"])

            declared = digests.VERBATIM_TRAILS | {("returned",)}
            with patch.object(verify, "VERBATIM_TRAILS", declared):
                report = verify_run(run_dir)

            self.assertEqual([], report["failures"])
            self.assertEqual("VALID", report["status"])

    def test_a_location_the_reviewer_returned_is_no_artefact_of_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))
            record = read_record(run_dir)
            review = record["iterations"][0]["review"]
            result = review["result"]
            result["findings"] = [{"path": "../outside.py", "sha256": "0" * 64}]
            review["result_sha256"] = sha256_json(result)
            rewrite_record(run_dir, record)

            report = verify_run(run_dir)

            self.assertEqual([], report["failures"])
            self.assertEqual("VALID", report["status"])


if __name__ == "__main__":
    unittest.main()
