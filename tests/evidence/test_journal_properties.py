"""What the run journal must hold for every file and every tampering.

The journal is the trajectory a run leaves behind, and the reason it is chained
is that someone may edit it afterwards. The cases beside these move one line
and name what breaks; these state that no move is the one that gets through.
"""

import json
import tempfile
import unittest
from pathlib import Path

from hypothesis import assume, given
from hypothesis import strategies as st

from codeservo.evidence.journal import JournalError, chain_failures, read_journal
from codeservo.evidence.verify import Verdict, verify_run
from run_fixtures import build_run, journal_lines, rewrite_journal


class ReadingProperties(unittest.TestCase):
    """A journal is read, or refused by name."""

    @given(text=st.text(max_size=128))
    def test_any_text_is_read_or_refused_by_name(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text(text, encoding="utf-8")
            try:
                events = read_journal(path)
            except JournalError:
                return
            self.assertIsInstance(events, list)

    @given(documents=st.lists(st.text(max_size=8), max_size=4))
    def test_a_line_that_is_not_an_object_is_refused(self, documents):
        """An event is an object, so a scalar on a line is not a short event."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text(
                "".join(f"{json.dumps(text)}\n" for text in documents), encoding="utf-8"
            )
            if not documents:
                self.assertEqual(read_journal(path), [])
                return
            with self.assertRaises(JournalError):
                read_journal(path)


class ChainProperties(unittest.TestCase):
    """Every reordering, alteration and removal is seen, whichever line it is."""

    def journal(self, root: Path) -> tuple[Path, list[str]]:
        run_dir = build_run(root)
        return run_dir, journal_lines(run_dir)

    @given(data=st.data())
    def test_swapping_any_two_lines_breaks_the_chain(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, lines = self.journal(Path(tmp))
            indices = st.integers(min_value=0, max_value=len(lines) - 1)
            first, second = data.draw(indices), data.draw(indices)
            assume(first != second)
            lines[first], lines[second] = lines[second], lines[first]
            rewrite_journal(run_dir, lines)
            self.assertTrue(chain_failures(read_journal(run_dir / "events.jsonl")))

    @given(data=st.data(), extra=st.text(min_size=1, max_size=8))
    def test_altering_any_line_breaks_the_chain(self, data, extra):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, lines = self.journal(Path(tmp))
            index = data.draw(st.integers(min_value=0, max_value=len(lines) - 1))
            event = json.loads(lines[index])
            assume(extra not in event)
            event[extra] = None
            lines[index] = json.dumps(event)
            rewrite_journal(run_dir, lines)
            self.assertTrue(chain_failures(read_journal(run_dir / "events.jsonl")))

    @given(data=st.data())
    def test_removing_any_line_but_the_last_breaks_the_chain(self, data):
        """A truncated journal is a shorter chain, and still a sound one.

        Removing the last line leaves every remaining link intact, so the chain
        alone cannot see it. What sees it is the record, which says how many
        events the run wrote and which digest closed them.
        """
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, lines = self.journal(Path(tmp))
            index = data.draw(st.integers(min_value=0, max_value=len(lines) - 2))
            del lines[index]
            rewrite_journal(run_dir, lines)
            self.assertTrue(chain_failures(read_journal(run_dir / "events.jsonl")))

    def test_removing_the_last_line_is_seen_by_the_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, lines = self.journal(Path(tmp))
            rewrite_journal(run_dir, lines[:-1])
            self.assertEqual(verify_run(run_dir)["status"], Verdict.INVALID)


if __name__ == "__main__":
    unittest.main()
