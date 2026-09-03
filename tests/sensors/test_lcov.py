"""Reading LCOV tracefiles, and projecting them onto the observation."""

import tempfile
import unittest
from pathlib import Path

from codeservo.sensors import lcov, reports
from codeservo.sensors.observations import Severity, Status, classify

TREE = Path("/tree")

# The shape coverage.py 7.16.0 writes, trimmed to two files: `SF:` relative to
# where the tool ran, `DA:` without a checksum, `BRDA:` whose branch is a
# phrase and whose count is `-` where it was never taken, `FN:` with three
# fields, and the summary lines this reader does not read.
COVERAGE_PY = b"""SF:src/app.py
FN:23,48,handle
FN:51,58,_place
FNDA:4,handle
FNDA:0,_place
FNF:2
FNH:1
DA:23,4
DA:24,4
DA:51,0
DA:52,0
BRDA:24,0,jump to line 25,4
BRDA:24,0,jump to line 27,-
BRF:2
BRH:1
LF:4
LH:2
end_of_record
SF:src/empty.py
DA:1,0
LF:1
LH:0
end_of_record
"""


def tracefile(*records: str) -> bytes:
    return "".join(f"{record}\n{lcov.RECORD_END}\n" for record in records).encode()


class CoveragePyTests(unittest.TestCase):
    """The one producer this reader was measured against."""

    def test_reads_the_shape_coverage_py_writes(self) -> None:
        report = lcov.parse_report(COVERAGE_PY, "coverage.info", tree=TREE)

        self.assertEqual(
            ["src/app.py", "src/empty.py"], [entry.declared for entry in report.files]
        )
        app = report.files[0]
        self.assertEqual({23: 4, 24: 4, 51: 0, 52: 0}, app.lines)
        self.assertEqual(
            {"24,0,jump to line 25": 4, "24,0,jump to line 27": 0}, app.branches
        )
        self.assertEqual({"handle": 4, "_place": 0}, app.functions)

    def test_counts_the_records_and_never_the_summary_lines(self) -> None:
        # Measured: LF, LH, BRF, BRH, FNF and FNH reproduce exactly the
        # records they summarise, so the records are what this counts. A
        # tracefile whose summaries lie is counted by what it listed.
        lying = COVERAGE_PY.replace(b"LF:4\nLH:2", b"LF:400\nLH:400")

        document = self._project(lying)

        self.assertEqual(5, document.metrics["lines"])
        self.assertEqual(2, document.metrics["lines_covered"])

    def _project(self, raw: bytes, *, passed: bool = True):
        report = lcov.parse_report(raw, "coverage.info", tree=TREE)
        return lcov.project(
            [report], sensor="coverage", passed=passed, pattern="*.info", left=0
        )

    def test_counts_every_family_and_states_the_share_of_each(self) -> None:
        document = self._project(COVERAGE_PY)

        self.assertEqual(
            {
                "files": 2,
                "lines": 5,
                "lines_covered": 2,
                "lines_missing": 3,
                "line_coverage": 40.0,
                "branches": 2,
                "branches_covered": 1,
                "branches_missing": 1,
                "branch_coverage": 50.0,
                "functions": 2,
                "functions_covered": 1,
                "functions_missing": 1,
                "function_coverage": 50.0,
            },
            document.metrics,
        )
        self.assertEqual(
            "40.00 percent of 5 lines, 50.00 percent of 2 branches,"
            " 50.00 percent of 2 functions over 2 files in 1 report",
            document.summary,
        )
        self.assertEqual(Status.PASSED, document.status)

    def test_a_file_no_test_reached_at_all_is_a_finding(self) -> None:
        document = self._project(COVERAGE_PY)

        self.assertEqual(
            [("uncovered:src/empty.py", Severity.INFO, "src/empty.py", None)],
            [(f.id, f.severity, f.path, f.line) for f in document.findings],
        )
        self.assertEqual(
            "no line covered of 1 instrumented in src/empty.py",
            document.findings[0].message,
        )


class RecordTests(unittest.TestCase):
    """What one record says, whichever version of the format wrote it."""

    def _file(self, record: str) -> lcov.FileCoverage:
        [entry] = lcov.parse_report(tracefile(record), "t.info", tree=TREE).files
        return entry

    def test_a_line_may_carry_the_checksum_a_producer_writes(self) -> None:
        entry = self._file("SF:a.py\nDA:1,3,f7c3bc1d808e04732adf679965ccc34c")

        self.assertEqual({1: 3}, entry.lines)

    def test_a_function_is_named_by_two_fields_or_by_three(self) -> None:
        for declaration in ("FN:23,handle", "FN:23,48,handle"):
            with self.subTest(declaration):
                entry = self._file(f"SF:a.py\n{declaration}\nFNDA:2,handle")

                self.assertEqual({"handle": 2}, entry.functions)

    def test_a_function_declared_and_never_called_is_found_and_not_covered(
        self,
    ) -> None:
        entry = self._file("SF:a.py\nFN:1,2,never\nFN:5,6,called\nFNDA:3,called")

        self.assertEqual({"never": 0, "called": 3}, entry.functions)

    def test_a_function_reported_without_being_declared_is_still_counted(self) -> None:
        entry = self._file("SF:a.py\nFNDA:3,called")

        self.assertEqual({"called": 3}, entry.functions)

    def test_a_branch_identifier_may_hold_anything_but_the_count(self) -> None:
        entry = self._file(
            "SF:a.py\nBRDA:7,0,jump to line 8,3\nBRDA:7,1,a,b,c,-\nBRDA:9,0,0,12"
        )

        self.assertEqual(
            {"7,0,jump to line 8": 3, "7,1,a,b,c": 0, "9,0,0": 12}, entry.branches
        )

    def test_what_one_record_says_twice_of_a_line_is_one_line(self) -> None:
        entry = self._file("SF:a.py\nDA:1,2\nDA:1,3\nDA:2,0")

        self.assertEqual({1: 5, 2: 0}, entry.lines)

    def test_a_prefix_this_reading_does_not_count_is_passed_over(self) -> None:
        entry = self._file(
            "TN:suite\nSF:a.py\nVER:1234\nFNL:0,1,2\nFNA:0,3,alias\nDA:1,1\nunknown"
        )

        self.assertEqual({1: 1}, entry.lines)
        self.assertEqual({}, entry.functions)


class SourcePathTests(unittest.TestCase):
    """Where a record's source file is, read against the measured tree."""

    def _entry(self, declared: str, tree: Path) -> lcov.FileCoverage:
        [entry] = lcov.parse_report(
            tracefile(f"SF:{declared}\nDA:1,1"), "t.info", tree=tree
        ).files
        return entry

    def test_a_relative_source_is_already_what_the_record_wants(self) -> None:
        self.assertEqual("src/app.py", self._entry("src/app.py", TREE).path)

    def test_an_absolute_source_inside_the_tree_is_relativised(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp)

            entry = self._entry(str(tree / "src" / "app.py"), tree)

            self.assertEqual("src/app.py", entry.path)

    def test_a_source_outside_the_tree_is_counted_and_points_nowhere(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp) / "tree"
            tree.mkdir()

            for declared in (str(Path(temp) / "elsewhere.py"), "../outside.py"):
                with self.subTest(declared):
                    entry = self._entry(declared, tree)

                    self.assertIsNone(entry.path)
                    self.assertEqual(declared, entry.declared)
                    self.assertEqual({1: 1}, entry.lines)


class MergeTests(unittest.TestCase):
    """What the same file says twice is one measurement."""

    def _project(self, *raws: bytes):
        return lcov.project(
            [
                lcov.parse_report(raw, f"t{n}.info", tree=TREE)
                for n, raw in enumerate(raws)
            ],
            sensor="coverage",
            passed=True,
            pattern="*.info",
            left=0,
        )

    def test_two_records_of_one_file_in_one_tracefile_are_one_file(self) -> None:
        raw = tracefile("SF:a.py\nDA:1,1\nDA:2,0", "SF:a.py\nDA:1,0\nDA:2,3")

        document = self._project(raw)

        self.assertEqual(1, document.metrics["files"])
        self.assertEqual(2, document.metrics["lines"])
        self.assertEqual(2, document.metrics["lines_covered"])

    def test_two_tracefiles_covering_one_file_are_one_file(self) -> None:
        document = self._project(
            tracefile("SF:a.py\nDA:1,1\nDA:2,0\nBRDA:1,0,x,1"),
            tracefile("SF:a.py\nDA:1,0\nDA:2,2\nBRDA:1,0,x,0"),
        )

        self.assertEqual(1, document.metrics["files"])
        self.assertEqual(2, document.metrics["lines"])
        self.assertEqual(2, document.metrics["lines_covered"])
        self.assertEqual(1, document.metrics["branches"])
        self.assertIn("over 1 file in 2 reports", document.summary)

    def test_two_tracefiles_covering_different_files_are_both(self) -> None:
        document = self._project(
            tracefile("SF:a.py\nDA:1,1"), tracefile("SF:b.py\nDA:1,0")
        )

        self.assertEqual(2, document.metrics["files"])
        self.assertEqual(2, document.metrics["lines"])
        self.assertEqual(1, document.metrics["lines_covered"])

    def test_merging_leaves_the_reports_it_merged_alone(self) -> None:
        raw = tracefile("SF:a.py\nDA:1,1")
        report = lcov.parse_report(raw, "t.info", tree=TREE)

        lcov.merged([report, report])

        self.assertEqual({1: 1}, report.files[0].lines)


class RefusalTests(unittest.TestCase):
    """What a file matching the pattern is not, named rather than guessed at."""

    def _refused(self, raw: bytes) -> str:
        with self.assertRaises(reports.ReportFault) as refused:
            lcov.parse_report(raw, "t.info", tree=TREE)
        return str(refused.exception)

    def test_refuses_a_tracefile_its_tool_did_not_finish_writing(self) -> None:
        # The reading that matters: a coverage tool killed halfway writes a
        # tracefile that stops inside a record, and reading it would report a
        # coverage taken over part of the tree as if it were the whole.
        wrong = self._refused(b"SF:a.py\nDA:1,1\nDA:2,1\n")

        self.assertIn("ends inside the record for a.py", wrong)
        self.assertIn("its tool did not finish writing it", wrong)

    def test_refuses_a_tracefile_naming_no_source_file(self) -> None:
        for raw in (b"", b"TN:suite\n", b"   \n\n"):
            with self.subTest(raw):
                self.assertIn(
                    "names no source file, so it measured nothing", self._refused(raw)
                )

    def test_refuses_records_that_do_not_open_and_close_in_turn(self) -> None:
        self.assertIn(
            "t.info:1 ends a record none began", self._refused(b"end_of_record\n")
        )
        self.assertIn(
            "t.info:2 begins a record while the one for a.py has not ended",
            self._refused(b"SF:a.py\nSF:b.py\nend_of_record\n"),
        )

    def test_refuses_a_count_that_is_not_one(self) -> None:
        for record, wrong in (
            ("SF:a.py\nDA:1,many", "names no execution count for a line: 'many'"),
            ("SF:a.py\nDA:one,1", "names no execution count for a line number"),
            ("SF:a.py\nDA:1", "names no line and count: DA:1"),
            ("SF:a.py\nBRDA:1,0,x,many", "names no execution count for a branch"),
            ("SF:a.py\nBRDA:1", "names no branch: BRDA:1"),
            ("SF:a.py\nFNDA:many,f", "names no execution count for a function"),
            ("SF:a.py\nFNDA:1", "names no function: FNDA:1"),
            ("SF:a.py\nFN:1,", "names no function: FN:1,"),
        ):
            with self.subTest(record):
                self.assertIn(wrong, self._refused(tracefile(record)))

    def test_refuses_bytes_that_are_not_text(self) -> None:
        self.assertIn(
            "t.info is not valid UTF-8", self._refused(b"SF:\xff\xfe\nend_of_record\n")
        )


class ProjectionTests(unittest.TestCase):
    """The tracefiles, as the document every reader of an observation expects."""

    def _project(self, raw: bytes | None = None, *, passed: bool = True, left: int = 0):
        parsed = (
            [lcov.parse_report(raw, "t.info", tree=TREE)] if raw is not None else []
        )
        return lcov.project(
            parsed, sensor="coverage", passed=passed, pattern="**/*.info", left=left
        )

    def test_a_family_the_tool_did_not_instrument_is_found_and_has_no_share(
        self,
    ) -> None:
        # Counting a family at zero rather than leaving it out is what lets a
        # ratchet on what was found catch instrumentation being turned off.
        document = self._project(tracefile("SF:a.py\nDA:1,1"))

        self.assertEqual(0, document.metrics["branches"])
        self.assertEqual(0, document.metrics["functions"])
        self.assertNotIn("branch_coverage", document.metrics)
        self.assertNotIn("function_coverage", document.metrics)
        self.assertEqual(
            "100.00 percent of 1 lines over 1 file in 1 report", document.summary
        )

    def test_a_tracefile_instrumenting_nothing_says_so(self) -> None:
        document = self._project(tracefile("SF:a.py"))

        self.assertEqual(
            "nothing instrumented over 1 file in 1 report", document.summary
        )
        self.assertEqual(0, document.metrics["lines"])
        self.assertNotIn("line_coverage", document.metrics)
        self.assertEqual((), document.findings)

    def test_the_status_is_the_verdict_the_exit_code_reached(self) -> None:
        for passed, status in ((True, Status.PASSED), (False, Status.FAILED)):
            with self.subTest(passed=passed):
                document = self._project(tracefile("SF:a.py\nDA:1,0"), passed=passed)

                self.assertEqual(status, document.status)

    def test_no_report_says_so_and_counts_nothing(self) -> None:
        document = self._project(left=3)

        self.assertEqual(
            "no coverage report matching **/*.info was written;"
            " 3 left from an earlier measurement, not read",
            document.summary,
        )
        self.assertEqual(0, document.metrics["lines"])
        self.assertEqual(0, document.metrics["files"])
        self.assertEqual((), document.findings)

    def test_renders_a_document_the_contract_accepts(self) -> None:
        for passed in (True, False):
            with self.subTest(passed=passed):
                document = self._project(COVERAGE_PY, passed=passed)

                self.assertEqual(
                    ("valid", None), classify(reports.render(document), passed=passed)
                )


class ProjectionThroughTheTreeTests(unittest.TestCase):
    """Reading the tracefiles a measurement wrote, and projecting them as one."""

    def test_reads_every_tracefile_the_measurement_wrote(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp)
            (tree / "one.info").write_bytes(tracefile("SF:a.py\nDA:1,1"))
            (tree / "two.info").write_bytes(tracefile("SF:b.py\nDA:1,0"))

            document = lcov.projection(
                tree,
                ["one.info", "two.info"],
                sensor="coverage",
                passed=True,
                pattern="*.info",
                left=0,
            )

            self.assertEqual(2, document.metrics["files"])
            self.assertEqual(50.0, document.metrics["line_coverage"])

    def test_one_unreadable_tracefile_refuses_the_whole_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp)
            (tree / "one.info").write_bytes(tracefile("SF:a.py\nDA:1,1"))
            (tree / "two.info").write_bytes(b"SF:b.py\nDA:1,1\n")

            with self.assertRaisesRegex(reports.ReportFault, "two.info ends inside"):
                lcov.projection(
                    tree,
                    ["one.info", "two.info"],
                    sensor="coverage",
                    passed=True,
                    pattern="*.info",
                    left=0,
                )


if __name__ == "__main__":
    unittest.main()
