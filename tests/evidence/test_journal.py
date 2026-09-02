import json
import tempfile
import unittest
from pathlib import Path

from codeservo.evidence.journal import (
    EVENT_FIELDS,
    EVENT_SCHEMA_VERSION,
    JOURNAL_NAME,
    Journal,
    JournalError,
    chain_failures,
    event_sha256,
    read_journal,
)
from codeservo.evidence.digests import sha256_file, sha256_json


def journal(root: Path, run_id: str = "20260901T110848639656Z") -> Journal:
    return Journal(root / JOURNAL_NAME, run_id)


def written(root: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (root / JOURNAL_NAME).read_text(encoding="utf-8").splitlines()
    ]


class JournalShapeTests(unittest.TestCase):
    def test_one_line_per_event_carrying_the_recorded_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            book = journal(root)

            book.record("run.started", {"base_commit": "abc"})
            book.record("run.finished", {"status": "ACCEPTED"})

            events = written(root)
            self.assertEqual(2, len(events))
            for event in events:
                self.assertEqual(set(EVENT_FIELDS), set(event))
                self.assertEqual(EVENT_SCHEMA_VERSION, event["schema_version"])
                self.assertEqual("20260901T110848639656Z", event["run_id"])
            self.assertEqual("run.started", events[0]["type"])
            self.assertEqual({"status": "ACCEPTED"}, events[1]["payload"])

    def test_sequence_starts_at_one_and_leaves_no_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            book = journal(root)

            for index in range(5):
                book.record(f"step.{index}")

            self.assertEqual([1, 2, 3, 4, 5], [e["sequence"] for e in written(root)])
            self.assertEqual(5, book.count)

    def test_each_event_chains_to_the_one_before_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            book = journal(root)

            book.record("run.started")
            book.record("gate.finished", {"name": "unit"})
            book.record("run.finished")

            events = written(root)
            self.assertIsNone(events[0]["previous_sha256"])
            self.assertEqual(events[0]["sha256"], events[1]["previous_sha256"])
            self.assertEqual(events[1]["sha256"], events[2]["previous_sha256"])
            self.assertEqual(events[-1]["sha256"], book.head_sha256)

    def test_the_digest_closes_the_event_without_itself(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            journal(root).record("gate.finished", {"passed": False})

            event = written(root)[0]
            stated = {key: value for key, value in event.items() if key != "sha256"}
            self.assertEqual(sha256_json(stated), event["sha256"])
            self.assertEqual(event_sha256(event), event["sha256"])

    def test_every_event_is_on_the_file_system_before_the_next_one(self) -> None:
        # Nothing is buffered: what a gate reads while it measures is every
        # transition the run has already taken.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            book = journal(root)
            seen = []

            for index in range(3):
                book.record(f"step.{index}")
                seen.append([event["type"] for event in written(root)])

            self.assertEqual(
                [["step.0"], ["step.0", "step.1"], ["step.0", "step.1", "step.2"]],
                seen,
            )


class JournalSummaryTests(unittest.TestCase):
    def test_describes_the_journal_as_it_stands(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            book = journal(root)
            book.record("run.started")
            book.record("run.finished")

            summary = book.summary()

            self.assertEqual(
                {"path", "count", "head_sha256", "file_sha256"}, set(summary)
            )
            self.assertEqual(JOURNAL_NAME, summary["path"])
            self.assertEqual(2, summary["count"])
            self.assertEqual(written(root)[-1]["sha256"], summary["head_sha256"])
            self.assertEqual(
                sha256_file(root / JOURNAL_NAME), summary["file_sha256"]
            )

    def test_an_unopened_journal_describes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            summary = journal(Path(temp)).summary()

            self.assertEqual(0, summary["count"])
            self.assertIsNone(summary["head_sha256"])
            self.assertIsNone(summary["file_sha256"])


class ChainReadingTests(unittest.TestCase):
    def _journal(self, root: Path) -> list[str]:
        book = journal(root)
        book.record("run.started")
        book.record("gate.finished", {"name": "unit", "passed": True})
        book.record("decision.recorded", {"status": "ACCEPTED"})
        book.record("run.finished", {"status": "ACCEPTED"})
        return (root / JOURNAL_NAME).read_text(encoding="utf-8").splitlines()

    def _aspects(self, root: Path, lines: list[str]) -> list[str]:
        (root / JOURNAL_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")
        events = read_journal(root / JOURNAL_NAME)
        return sorted({aspect for aspect, _ in chain_failures(events)})

    def test_an_intact_journal_disagrees_with_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lines = self._journal(root)

            self.assertEqual([], self._aspects(root, lines))

    def test_reordered_lines_break_the_sequence_and_the_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lines = self._journal(root)
            lines[1], lines[2] = lines[2], lines[1]

            self.assertEqual(["chain", "sequence"], self._aspects(root, lines))

    def test_a_removed_line_breaks_the_sequence_and_the_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lines = self._journal(root)
            del lines[1]

            self.assertEqual(["chain", "sequence"], self._aspects(root, lines))

    def test_an_altered_payload_breaks_the_digest_of_its_own_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lines = self._journal(root)
            event = json.loads(lines[1])
            event["payload"]["passed"] = False
            lines[1] = json.dumps(event, sort_keys=True, separators=(",", ":"))

            self.assertEqual(["digests"], self._aspects(root, lines))

    def test_a_line_missing_a_field_is_read_as_a_shape_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lines = self._journal(root)
            event = json.loads(lines[0])
            del event["payload"]
            lines[0] = json.dumps(event, sort_keys=True, separators=(",", ":"))

            self.assertEqual(["chain", "shape"], self._aspects(root, lines))

    def test_a_journal_of_another_run_names_the_run_it_belongs_to(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._journal(root)
            events = read_journal(root / JOURNAL_NAME)

            failures = chain_failures(events, "another-run")

            self.assertTrue(all(aspect == "shape" for aspect, _ in failures))
            self.assertTrue(
                all(JOURNAL_NAME in statement for _, statement in failures)
            )

    def test_an_unreadable_line_names_the_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / JOURNAL_NAME).write_text("{not json}\n", encoding="utf-8")

            with self.assertRaises(JournalError) as raised:
                read_journal(root / JOURNAL_NAME)

            self.assertIn(JOURNAL_NAME, str(raised.exception))

    def test_a_journal_that_is_not_there_names_the_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(JournalError) as raised:
                read_journal(Path(temp) / JOURNAL_NAME)

            self.assertIn(JOURNAL_NAME, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
