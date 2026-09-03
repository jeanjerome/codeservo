"""The findings register: one tabulated line per finding that landed."""

import tempfile
import unittest
from pathlib import Path

from codeservo.evidence.register import (
    COLUMNS,
    NOT_COVERED,
    append_rows,
    cell,
    register_path,
)

ROW = {
    "landed_at": "2026-09-03T18:00:00+00:00",
    "repo": "repo",
    "run_id": "20260903T180000000000Z",
    "commit": "a" * 40,
    "severity": "minor",
    "path": "app.py",
    "line": 2,
    "message": "a note",
    "evidence": "app.py:2",
    "covered_by": NOT_COVERED,
}


class RegisterTests(unittest.TestCase):
    def test_one_register_per_repository_beside_its_runs(self) -> None:
        self.assertEqual(
            Path("state", "findings", "repo.tsv"),
            register_path(Path("state"), "repo"),
        )

    def test_the_header_is_written_once_and_rows_follow_in_column_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "findings", "repo.tsv")

            append_rows(path, [ROW])
            append_rows(path, [{**ROW, "line": None, "message": "another"}])

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual("\t".join(COLUMNS), lines[0])
            self.assertEqual(
                "\t".join(str(ROW[column]) for column in COLUMNS), lines[1]
            )
            self.assertEqual(3, len(lines))
            self.assertEqual("", lines[2].split("\t")[COLUMNS.index("line")])
            self.assertEqual("another", lines[2].split("\t")[COLUMNS.index("message")])

    def test_nothing_to_append_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "repo.tsv")

            append_rows(path, [])

            self.assertFalse(path.exists())

    def test_a_cell_keeps_its_line_and_its_column(self) -> None:
        """A message may carry anything a reviewer typed; a row may not."""
        self.assertEqual("a\\tb\\nc\\rd\\\\e", cell("a\tb\nc\rd\\e"))
        self.assertEqual("", cell(None))
        self.assertEqual("2", cell(2))


if __name__ == "__main__":
    unittest.main()
