"""Which report files a measurement wrote, and what a reader is handed."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from codeservo.sensors import reports
from codeservo.sensors.observations import (
    SCHEMA_VERSION,
    Finding,
    Observation,
    Severity,
    Status,
    classify,
)


class ListingTests(unittest.TestCase):
    """Reading the tree for reports, without a clock and without writing."""

    def _tree(self, root: Path) -> Path:
        tree = root / "tree"
        (tree / "a" / "target" / "surefire-reports").mkdir(parents=True)
        (tree / "b" / "target" / "surefire-reports").mkdir(parents=True)
        return tree

    def test_lists_every_matching_file_relative_to_the_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tree = self._tree(Path(temp))
            (tree / "a/target/surefire-reports/TEST-one.xml").write_text("<testsuite/>")
            (tree / "b/target/surefire-reports/TEST-two.xml").write_text("<testsuite/>")
            (tree / "b/target/surefire-reports/two.txt").write_text("text")

            listing = reports.list_reports(
                tree, "**/target/surefire-reports/TEST-*.xml"
            )

            self.assertEqual(
                [
                    "a/target/surefire-reports/TEST-one.xml",
                    "b/target/surefire-reports/TEST-two.xml",
                ],
                sorted(listing),
            )
            for size, _ in listing.values():
                self.assertEqual(len("<testsuite/>"), size)

    def test_a_link_leaving_the_tree_is_not_under_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tree = self._tree(root)
            outside = root / "elsewhere" / "TEST-out.xml"
            outside.parent.mkdir()
            outside.write_text("<testsuite/>")
            os.symlink(outside, tree / "a/target/surefire-reports/TEST-linked.xml")

            self.assertEqual({}, reports.list_reports(tree, "**/TEST-*.xml"))

    def test_written_means_new_or_rewritten_since_the_listing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tree = self._tree(Path(temp))
            kept = tree / "a/target/surefire-reports/TEST-kept.xml"
            rewritten = tree / "a/target/surefire-reports/TEST-rewritten.xml"
            kept.write_text("<testsuite/>")
            rewritten.write_text("<testsuite/>")
            os.utime(rewritten, ns=(1_000_000_000, 1_000_000_000))
            before = reports.list_reports(tree, "**/TEST-*.xml")
            # The gate runs: one report rewritten byte for byte, one added,
            # one left alone.
            rewritten.write_text("<testsuite/>")
            (tree / "b/target/surefire-reports/TEST-new.xml").write_text("<testsuite/>")

            written, left = reports.written_reports(tree, "**/TEST-*.xml", before)

            self.assertEqual(
                [
                    "a/target/surefire-reports/TEST-rewritten.xml",
                    "b/target/surefire-reports/TEST-new.xml",
                ],
                written,
            )
            self.assertEqual(["a/target/surefire-reports/TEST-kept.xml"], left)

    def test_a_pattern_matching_nothing_lists_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tree = self._tree(Path(temp))

            self.assertEqual({}, reports.list_reports(tree, "nowhere/*.xml"))
            self.assertEqual(
                ([], []), reports.written_reports(tree, "nowhere/*.xml", {})
            )


class ReadingTests(unittest.TestCase):
    """Handing a reader the bytes of one report, or refusing to."""

    def test_reads_the_bytes_the_tool_wrote(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp)
            (tree / "report.xml").write_bytes(b"<testsuite/>")

            self.assertEqual(b"<testsuite/>", reports.read_report(tree, "report.xml"))

    def test_refuses_by_size_before_reading_any_of_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp)
            (tree / "huge.xml").write_bytes(b"x" * 40)

            with self.assertRaisesRegex(
                reports.ReportFault, "huge.xml is larger than 10 bytes"
            ):
                reports.read_report(tree, "huge.xml", limit=10)

    def test_a_report_the_file_system_will_not_hand_over_is_a_fault(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(
                reports.ReportFault, "gone.xml could not be read"
            ):
                reports.read_report(Path(temp), "gone.xml")

    def test_the_shipped_limit_is_larger_than_any_report_and_finite(self) -> None:
        self.assertEqual(64 * 1024 * 1024, reports.REPORT_SIZE_LIMIT)


class OneLineTests(unittest.TestCase):
    """What a finding carries of what a tool said."""

    def test_takes_the_first_non_empty_line_and_collapses_it(self) -> None:
        self.assertEqual("a b", reports.one_line("\n\n  a   b  \nsecond\n"))
        self.assertEqual("only", reports.one_line("only"))

    def test_a_text_with_nothing_in_it_says_nothing(self) -> None:
        for empty in (None, "", "   ", "\n\t\n"):
            with self.subTest(repr(empty)):
                self.assertEqual("", reports.one_line(empty))


class UniqueTests(unittest.TestCase):
    """One finding is one thing seen once."""

    def test_numbers_a_name_already_taken(self) -> None:
        taken: set[str] = set()

        named = [reports.unique("rule", taken) for _ in range(3)]

        self.assertEqual(["rule", "rule#2", "rule#3"], named)
        self.assertEqual({"rule", "rule#2", "rule#3"}, taken)

    def test_a_name_colliding_with_a_variant_takes_the_next_one(self) -> None:
        taken = {"rule#2"}

        self.assertEqual("rule", reports.unique("rule", taken))
        self.assertEqual("rule#3", reports.unique("rule", taken))


class RenderTests(unittest.TestCase):
    """The one spelling a projected document is kept in."""

    def _observation(self, status: Status) -> Observation:
        return Observation(
            schema_version=SCHEMA_VERSION,
            sensor="unit",
            status=status,
            summary="1 test, 1 failure",
            findings=(
                Finding(
                    id="c.t",
                    severity=Severity.MAJOR,
                    path="a.py",
                    line=3,
                    message="failed: no",
                ),
            ),
            metrics={"tests": 1.0},
        )

    def test_renders_a_document_the_contract_accepts(self) -> None:
        for status, passed in ((Status.PASSED, True), (Status.FAILED, False)):
            with self.subTest(status):
                raw = reports.render(self._observation(status))

                self.assertEqual(("valid", None), classify(raw, passed=passed))
                self.assertTrue(raw.endswith(b"\n"))

    def test_the_spelling_is_stable_and_sorted(self) -> None:
        raw = reports.render(self._observation(Status.PASSED))

        document = json.loads(raw)
        self.assertEqual(
            ["findings", "metrics", "schema_version", "sensor", "status", "summary"],
            list(document),
        )
        self.assertEqual(raw, reports.render(self._observation(Status.PASSED)))


if __name__ == "__main__":
    unittest.main()
