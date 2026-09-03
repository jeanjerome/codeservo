"""What reading a report must hold for every document a tool could write.

A report arrives from a tool the controller did not write, so its bytes are
whatever that tool produced. The reader has two honest answers to all of them,
a report or a refusal naming what is wrong, and a traceback is neither: it
would end the run where no decision was recorded and nothing says which file
was at fault.
"""

import json
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from codeservo.sensors import junit, lcov, sarif
from codeservo.sensors.observations import Classification, classify
from codeservo.sensors.reports import ReportFault, render
from properties import json_documents, json_objects

TREE = Path("/tree")

LEVELS = st.sampled_from([str(level) for level in sarif.Level]) | st.text(max_size=4)
LOCATIONS = st.lists(
    st.fixed_dictionaries(
        {
            "physicalLocation": st.fixed_dictionaries(
                {
                    "artifactLocation": st.fixed_dictionaries(
                        {"uri": st.text(max_size=12)}
                    ),
                    "region": st.fixed_dictionaries(
                        {"startLine": st.integers(min_value=-3, max_value=99)}
                    ),
                }
            )
        }
    ),
    max_size=2,
)
RESULTS = st.lists(
    st.fixed_dictionaries(
        {
            "ruleId": st.text(max_size=6),
            "level": LEVELS,
            "message": st.fixed_dictionaries({"text": st.text(max_size=12)}),
            "locations": LOCATIONS,
        }
    ),
    max_size=4,
)


def logs() -> st.SearchStrategy[dict]:
    """A SARIF log of the shape a tool writes, with values a tool may write."""
    return st.fixed_dictionaries(
        {
            "version": st.just(sarif.VERSION),
            "runs": st.lists(
                st.fixed_dictionaries(
                    {
                        "tool": st.fixed_dictionaries(
                            {
                                "driver": st.fixed_dictionaries(
                                    {"name": st.text(max_size=6)}
                                )
                            }
                        ),
                        "results": RESULTS,
                    }
                ),
                max_size=2,
            ),
        }
    )


class SarifReadingProperties(unittest.TestCase):
    """Every document is a report or a refusal, and never a traceback."""

    @given(document=json_documents())
    def test_any_document_is_read_or_refused_by_name(self, document) -> None:
        raw = json.dumps(document).encode("utf-8")
        try:
            sarif.parse_report(raw, "r.sarif", tree=TREE)
        except ReportFault as fault:
            self.assertIn("r.sarif", str(fault))

    @given(document=json_objects())
    def test_any_object_is_read_or_refused_by_name(self, document) -> None:
        document["version"] = sarif.VERSION
        raw = json.dumps(document).encode("utf-8")
        try:
            sarif.parse_report(raw, "r.sarif", tree=TREE)
        except ReportFault as fault:
            self.assertIn("r.sarif", str(fault))

    @given(raw=st.binary(max_size=64))
    def test_any_bytes_are_read_or_refused_by_name(self, raw) -> None:
        try:
            sarif.parse_report(raw, "r.sarif", tree=TREE)
        except ReportFault as fault:
            self.assertIn("r.sarif", str(fault))

    @given(document=logs())
    def test_a_log_of_that_shape_is_always_read(self, document) -> None:
        """A document shaped the way the format defines it is never refused."""
        raw = json.dumps(document).encode("utf-8")

        report = sarif.parse_report(raw, "r.sarif", tree=TREE)

        counted = sum(len(run["results"]) for run in document["runs"])
        self.assertEqual(counted, len(report.results))
        for result in report.results:
            self.assertIn(result.level, sarif.Level)
            self.assertTrue(result.rule)
            self.assertTrue(result.message)

    @given(document=logs(), passed=st.booleans())
    def test_whatever_is_read_projects_onto_the_contract(
        self, document, passed
    ) -> None:
        """The projection is a document the observation contract accepts.

        The controller writes it, so a projection the contract refuses would
        be the controller failing its own gate reader.
        """
        report = sarif.parse_report(
            json.dumps(document).encode("utf-8"), "r.sarif", tree=TREE
        )
        projected = sarif.project(
            [report], sensor="lint", passed=passed, pattern="*.sarif", left=0
        )

        status, error = classify(render(projected), passed=passed)
        self.assertEqual((Classification.VALID, None), (status, error))
        metrics = projected.metrics
        self.assertEqual(len(report.results), metrics["results"])
        self.assertGreaterEqual(
            metrics["results"],
            metrics["errors"] + metrics["warnings"] + metrics["notes"],
        )


class JunitReadingProperties(unittest.TestCase):
    """The same two answers, for the reader beside it."""

    @given(raw=st.binary(max_size=64))
    def test_any_bytes_are_read_or_refused_by_name(self, raw) -> None:
        try:
            junit.parse_report(raw, "r.xml")
        except ReportFault as fault:
            self.assertIn("r.xml", str(fault))

    @given(text=st.text(max_size=64))
    def test_any_text_is_read_or_refused_by_name(self, text) -> None:
        try:
            junit.parse_report(text.encode("utf-8"), "r.xml")
        except ReportFault as fault:
            self.assertIn("r.xml", str(fault))

    @given(
        cases=st.lists(
            st.fixed_dictionaries(
                {
                    "classname": st.text(max_size=6),
                    "name": st.text(max_size=6),
                    "verdict": st.sampled_from(["", "failure", "error", "skipped"]),
                }
            ),
            max_size=4,
        ),
        passed=st.booleans(),
    )
    def test_whatever_is_read_projects_onto_the_contract(self, cases, passed) -> None:
        elements = "".join(
            f"<testcase classname='c{index}' name='n{index}'>"
            + (f"<{case['verdict']} message='m'/>" if case["verdict"] else "")
            + "</testcase>"
            for index, case in enumerate(cases)
        )
        raw = f"<testsuite time='0.1'>{elements}</testsuite>".encode()

        report = junit.parse_report(raw, "r.xml")
        projected = junit.project(
            [report], sensor="unit", passed=passed, pattern="*.xml", left=0
        )

        self.assertEqual(
            (Classification.VALID, None), classify(render(projected), passed=passed)
        )
        self.assertEqual(len(cases), projected.metrics["tests"])


class LcovReadingProperties(unittest.TestCase):
    """The same two answers, for the reader of a coverage tracefile."""

    LINES = st.lists(
        st.tuples(
            st.integers(min_value=1, max_value=40),
            st.integers(min_value=0, max_value=9),
        ),
        max_size=6,
    )

    @given(raw=st.binary(max_size=64))
    def test_any_bytes_are_read_or_refused_by_name(self, raw) -> None:
        try:
            lcov.parse_report(raw, "t.info", tree=TREE)
        except ReportFault as fault:
            self.assertIn("t.info", str(fault))

    @given(text=st.text(max_size=64))
    def test_any_text_is_read_or_refused_by_name(self, text) -> None:
        try:
            lcov.parse_report(text.encode("utf-8"), "t.info", tree=TREE)
        except ReportFault as fault:
            self.assertIn("t.info", str(fault))

    @given(
        records=st.lists(
            st.tuples(st.text(alphabet="ab/.", min_size=1, max_size=6), LINES),
            min_size=1,
            max_size=3,
        ),
        passed=st.booleans(),
    )
    def test_whatever_is_read_projects_onto_the_contract(self, records, passed) -> None:
        text = ""
        for name, lines in records:
            text += f"SF:{name}\n"
            text += "".join(f"DA:{line},{count}\n" for line, count in lines)
            text += "end_of_record\n"

        report = lcov.parse_report(text.encode("utf-8"), "t.info", tree=TREE)
        projected = lcov.project(
            [report], sensor="coverage", passed=passed, pattern="*.info", left=0
        )

        self.assertEqual(
            (Classification.VALID, None), classify(render(projected), passed=passed)
        )
        metrics = projected.metrics
        # One file is one file however many records named it, and a line is
        # covered or missing and never both.
        self.assertEqual(len({name for name, _ in records}), metrics["files"])
        self.assertEqual(
            metrics["lines"], metrics["lines_covered"] + metrics["lines_missing"]
        )
        self.assertEqual("line_coverage" in metrics, metrics["lines"] > 0)


if __name__ == "__main__":
    unittest.main()
