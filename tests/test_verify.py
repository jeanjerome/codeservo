"""Verification of one run directory, against records built by hand.

A record written here is shaped exactly as the controller writes one, so a
case can move a single digest, artefact or journal line and watch the
verification report what that move broke.
"""

import json
import tempfile
import unittest
from pathlib import Path

from codeservo.events import JOURNAL_NAME, Journal
from codeservo.evidence import (
    sha256_file,
    sha256_json,
    sha256_path,
    sha256_record,
    sha256_text,
    write_json,
)
from codeservo.verify import (
    JOURNAL_EVIDENCE_VERSION,
    REPORT_SCHEMA_VERSION,
    VerificationError,
    render_report,
    verify_run,
)

RUN_ID = "20260901T110848639656Z"
TASK = "# Task\n\n- [AC1] `value()` returns `2`.\n"
CONSTITUTION = 'version = 1\n\n[review]\nblocking_severities = ["blocker"]\n'
PATCH = "diff --git a/app.py b/app.py\n"
REVIEW = {
    "criteria": [{"id": "AC1", "status": "satisfied", "evidence": "app.py"}],
    "findings": [],
}


def _gate(run_dir: Path, phase: str, name: str, *, passed: bool = True) -> dict:
    """One gate result, with the two logs the measurement left behind."""
    out_dir = run_dir / phase
    out_dir.mkdir(parents=True, exist_ok=True)
    for stream in ("stdout", "stderr"):
        (out_dir / f"{name}.{stream}.log").write_text(
            f"{name} {stream}\n", encoding="utf-8"
        )
    record = {
        "name": name,
        "command": "true",
        "passed": passed,
        "exit_code": 0 if passed else 1,
        "timed_out": False,
        "duration_ms": 12,
        "stdout_path": f"{phase}/{name}.stdout.log",
        "stdout_sha256": sha256_file(out_dir / f"{name}.stdout.log"),
        "stderr_path": f"{phase}/{name}.stderr.log",
        "stderr_sha256": sha256_file(out_dir / f"{name}.stderr.log"),
    }
    record["result_sha256"] = sha256_record(record)
    return record


def build_run(
    root: Path,
    *,
    status: str = "ACCEPTED",
    reasons: tuple[str, ...] = (),
    schema_version: int = JOURNAL_EVIDENCE_VERSION,
    journal: bool = True,
) -> Path:
    """A complete run directory, recorded the way the controller records one."""
    run_dir = root / "run"
    (run_dir / "environment").mkdir(parents=True)
    (run_dir / "TASK.md").write_text(TASK, encoding="utf-8")
    (run_dir / "constitution.toml").write_text(CONSTITUTION, encoding="utf-8")
    (run_dir / "change.patch").write_text(PATCH, encoding="utf-8")

    sensor = run_dir / "sensors" / "task-outcome"
    sensor.mkdir(parents=True)
    (sensor / "README.md").write_text("Controller-owned sensor.\n", encoding="utf-8")

    packages = run_dir / "environment" / "packages.json"
    write_json(packages, [{"name": "python", "version": "3.12.0"}])

    iteration_dir = run_dir / "iterations" / "01"
    (iteration_dir / "agent").mkdir(parents=True)
    (iteration_dir / "prompt.md").write_text("implement\n", encoding="utf-8")
    (iteration_dir / "agent" / "events.jsonl").write_text("{}\n", encoding="utf-8")
    agent = {
        "exit_code": 0,
        "duration_ms": 40,
        "events_path": "iterations/01/agent/events.jsonl",
        "events_sha256": sha256_file(iteration_dir / "agent" / "events.jsonl"),
    }
    agent["result_sha256"] = sha256_record(agent)

    review_dir = run_dir / "review"
    review_dir.mkdir()
    (review_dir / "prompt.md").write_text("review\n", encoding="utf-8")
    write_json(review_dir / "result.json", REVIEW)
    observations = {"schema_version": 1, "gates": []}
    meta = {
        "exit_code": 0,
        "result_path": "review/result.json",
        "result_sha256": sha256_file(review_dir / "result.json"),
    }
    meta["meta_sha256"] = sha256_record(meta)

    book = Journal(run_dir / JOURNAL_NAME, RUN_ID) if journal else None
    if book is not None:
        book.record("run.started", {"base_commit": "abc"})
        book.record("inputs.frozen", {"task_sha256": sha256_text(TASK)})

    baseline = [_gate(run_dir, "baseline", "unit")]
    quick = [_gate(run_dir, "iterations/01/quick", "unit")]
    full = [_gate(run_dir, "full", "compile")]
    record = {
        "schema_version": schema_version,
        "run_id": RUN_ID,
        "base_commit": "abc",
        "task_sha256": sha256_text(TASK),
        "constitution_sha256": sha256_text(CONSTITUTION),
        "sensors": {
            "task-outcome": {
                "path": "sensors/task-outcome",
                "reference": "test/task-outcome",
                "sha256": sha256_path(sensor),
            }
        },
        "environment": {
            "provider": "pixi",
            "manifest_path": "pyproject.toml",
            "manifest_sha256": sha256_text("[workspace]\n"),
            "lock_path": "pixi.lock",
            "lock_sha256": sha256_text("version: 6\n"),
            "packages_path": "environment/packages.json",
            "packages_sha256": sha256_file(packages),
        },
        "baseline": baseline,
        "iterations": [
            {
                "iteration": 1,
                "prompt": {
                    "path": "iterations/01/prompt.md",
                    "sha256": sha256_text("implement\n"),
                },
                "agent": agent,
                "quick_gates": quick,
            }
        ],
        "full_gates": full,
        "review": {
            "prompt": {
                "path": "review/prompt.md",
                "sha256": sha256_text("review\n"),
            },
            "observations": observations,
            "observations_sha256": sha256_json(observations),
            "result": REVIEW,
            "result_sha256": sha256_json(REVIEW),
            "meta": meta,
        },
        "status": status,
        "decision": {"reasons": list(reasons)},
        "patch_sha256": sha256_text(PATCH),
        "run_dir": ".",
    }
    if book is not None:
        measured = (("baseline", baseline), ("quick", quick), ("full", full))
        for phase, results in measured:
            for result in results:
                book.record(
                    "gate.finished",
                    {
                        "phase": phase,
                        "name": result["name"],
                        "passed": result["passed"],
                        "result_sha256": result["result_sha256"],
                    },
                )
        if status != "RUNNING":
            book.record(
                "decision.recorded", {"status": status, "reasons": list(reasons)}
            )
            book.record("run.finished", {"status": status})
        record["events"] = book.summary()
    write_json(run_dir / "evidence.json", record)
    return run_dir


def read_record(run_dir: Path) -> dict:
    return json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))


def rewrite_record(run_dir: Path, record: dict) -> None:
    write_json(run_dir / "evidence.json", record)


def journal_lines(run_dir: Path) -> list[str]:
    return (run_dir / JOURNAL_NAME).read_text(encoding="utf-8").splitlines()


def rewrite_journal(run_dir: Path, lines: list[str]) -> None:
    (run_dir / JOURNAL_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")


def named(report: dict, name: str) -> dict:
    return next(check for check in report["checks"] if check["name"] == name)


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
                    "artifact.review/result.json",
                    "artifact.change.patch",
                    "digest.baseline.0",
                    "digest.iterations.1.quick_gates.0",
                    "digest.full_gates.0",
                    "digest.iterations.1.agent",
                    "digest.review.result",
                    "digest.review.observations",
                    "digest.review.meta",
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
            record["full_gates"][0]["passed"] = False
            rewrite_record(run_dir, record)

            report = self._report(run_dir)

            self.assertEqual(
                ["full_gates.0.result_sha256: does not describe what the record holds"],
                report["failures"],
            )

    def test_a_reviewer_result_that_was_edited_after_the_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))
            record = read_record(run_dir)
            record["review"]["result"]["findings"] = [{"severity": "blocker"}]
            rewrite_record(run_dir, record)

            report = self._report(run_dir)

            self.assertEqual(
                ["review.result_sha256: does not describe what the record holds"],
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


if __name__ == "__main__":
    unittest.main()
