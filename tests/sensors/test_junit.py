"""Reading JUnit XML reports, and projecting them onto the observation."""

import json
import unittest

from codeservo.sensors import junit, reports
from codeservo.sensors.observations import Observation, Status, classify

SUREFIRE = b"""<?xml version="1.0" encoding="UTF-8"?>
<testsuite xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" version="3.0.2"
 name="io.demo.CucumberRunnerTest" time="2.689" tests="4" errors="1" skipped="1" failures="1">
  <properties><property name="java.version" value="21.0.2"/></properties>
  <testcase name="Get a user" classname="io.demo.CucumberRunnerTest" time="0.5"/>
  <testcase name="Remove a user" classname="io.demo.CucumberRunnerTest" time="0.03">
    <failure message="The user should no longer exist ==&gt; expected: &lt;404&gt; but was: &lt;200&gt;" type="org.opentest4j.AssertionFailedError"><![CDATA[org.opentest4j.AssertionFailedError: The user should no longer exist
\tat io.demo.Steps.removed(Steps.java:148)
]]></failure>
  </testcase>
  <testcase name="Broken" classname="io.demo.CucumberRunnerTest" time="0.01">
    <error type="java.lang.IllegalStateException"><![CDATA[java.lang.IllegalStateException: no context
\tat io.demo.Steps.setUp(Steps.java:20)
]]></error>
  </testcase>
  <testcase name="Later" classname="io.demo.CucumberRunnerTest" time="0">
    <skipped message="not yet"/>
  </testcase>
</testsuite>
"""

PYTEST = b"""<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="1" skipped="0" tests="2" time="0.120">
    <testcase classname="tests.test_app" name="test_value" file="tests/test_app.py" line="7" time="0.001"/>
    <testcase classname="tests.test_app" name="test_other" file="tests/test_app.py" line="12" time="0.002">
      <failure message="assert 1 == 2">def test_other():
&gt;       assert value() == 2
E       assert 1 == 2</failure>
    </testcase>
  </testsuite>
</testsuites>
"""


class ParseTests(unittest.TestCase):
    """One report as its tool wrote it, whichever tool that was."""

    def test_reads_a_surefire_report_case_by_case(self) -> None:
        report = junit.parse_report(SUREFIRE, "target/surefire-reports/TEST-x.xml")

        self.assertEqual("target/surefire-reports/TEST-x.xml", report.path)
        self.assertEqual(2.689, report.seconds)
        self.assertEqual(
            [
                ("Get a user", junit.Outcome.PASSED, ""),
                (
                    "Remove a user",
                    junit.Outcome.FAILED,
                    "The user should no longer exist ==> expected: <404> but was: <200>",
                ),
                # No message attribute: the first line of what the tool wrote.
                (
                    "Broken",
                    junit.Outcome.ERROR,
                    "java.lang.IllegalStateException: no context",
                ),
                ("Later", junit.Outcome.SKIPPED, "not yet"),
            ],
            [(case.name, case.outcome, case.message) for case in report.cases],
        )
        self.assertEqual(
            {"io.demo.CucumberRunnerTest"}, {case.classname for case in report.cases}
        )
        # Surefire names no file, so no case points anywhere.
        self.assertEqual({(None, None)}, {(c.path, c.line) for c in report.cases})

    def test_reads_suites_under_a_testsuites_root_and_where_a_case_points(self) -> None:
        report = junit.parse_report(PYTEST, "junit.xml")

        self.assertEqual(0.12, report.seconds)
        self.assertEqual(
            [
                ("test_value", "tests/test_app.py", 7),
                ("test_other", "tests/test_app.py", 12),
            ],
            [(case.name, case.path, case.line) for case in report.cases],
        )
        self.assertEqual("assert 1 == 2", report.cases[1].message)

    def test_a_location_outside_the_tree_is_no_location(self) -> None:
        for declared in ("/abs/test_app.py", "../test_app.py", "  "):
            with self.subTest(declared):
                raw = (
                    "<testsuite><testcase name='t' classname='c'"
                    f" file='{declared}' line='3'/></testsuite>"
                ).encode()

                [case] = junit.parse_report(raw, "r.xml").cases

                self.assertIsNone(case.path)
                self.assertEqual(3, case.line)

    def test_a_line_that_is_not_a_positive_integer_is_no_line(self) -> None:
        for declared in ("0", "-1", "x", ""):
            with self.subTest(declared):
                raw = (
                    "<testsuite><testcase name='t' classname='c'"
                    f" file='a.py' line='{declared}'/></testsuite>"
                ).encode()

                [case] = junit.parse_report(raw, "r.xml").cases

                self.assertEqual("a.py", case.path)
                self.assertIsNone(case.line)

    def test_a_verdict_element_without_any_text_is_named_by_what_it_is(self) -> None:
        raw = b"<testsuite><testcase name='t' classname='c'><failure/></testcase></testsuite>"

        [case] = junit.parse_report(raw, "r.xml").cases

        self.assertEqual(junit.Outcome.FAILED, case.outcome)
        self.assertEqual("failed", case.message)

    def test_an_unreadable_time_counts_for_nothing(self) -> None:
        raw = b"<testsuite time='soon'><testcase name='t' classname='c'/></testsuite>"

        self.assertEqual(0.0, junit.parse_report(raw, "r.xml").seconds)

    def test_refuses_what_is_not_a_report(self) -> None:
        for raw, wrong in (
            (b"<testsuite><testcase", "r.xml is not well-formed XML"),
            (
                b"<html><body/></html>",
                "r.xml is not a JUnit report: its root element is html",
            ),
            (
                b"<!DOCTYPE x [<!ENTITY a 'b'>]><testsuite/>",
                "r.xml declares a DTD or an entity, which no report does",
            ),
            (b"\xff\xfe<testsuite/>", "r.xml is not well-formed XML"),
        ):
            with self.subTest(wrong):
                with self.assertRaisesRegex(junit.ReportFault, wrong):
                    junit.parse_report(raw, "r.xml")


class ProjectionTests(unittest.TestCase):
    """The reports as the document every reader of an observation expects."""

    def _project(
        self, *raws: bytes, passed: bool = False, left: int = 0
    ) -> Observation:
        reports = [junit.parse_report(raw, f"r{n}.xml") for n, raw in enumerate(raws)]
        return junit.project(
            reports, sensor="test", passed=passed, pattern="**/TEST-*.xml", left=left
        )

    def test_counts_every_case_and_names_each_one_that_did_not_pass(self) -> None:
        document = self._project(SUREFIRE, PYTEST)

        self.assertEqual("test", document.sensor)
        self.assertEqual(Status.FAILED, document.status)
        self.assertEqual(
            "6 tests, 2 failures, 1 errors, 1 skipped in 2 reports", document.summary
        )
        self.assertEqual(
            {"tests": 6, "failures": 2, "errors": 1, "skipped": 1, "seconds": 2.809},
            document.metrics,
        )
        self.assertEqual(
            [
                (
                    "io.demo.CucumberRunnerTest.Remove a user",
                    "major",
                    None,
                    None,
                    "failed: The user should no longer exist ==> expected: <404> but was: <200>",
                ),
                (
                    "io.demo.CucumberRunnerTest.Broken",
                    "major",
                    None,
                    None,
                    "error: java.lang.IllegalStateException: no context",
                ),
                (
                    "tests.test_app.test_other",
                    "major",
                    "tests/test_app.py",
                    12,
                    "failed: assert 1 == 2",
                ),
            ],
            [(f.id, f.severity, f.path, f.line, f.message) for f in document.findings],
        )

    def test_the_status_is_the_verdict_the_exit_code_reached(self) -> None:
        # A suite told to ignore failures passes with failures on record, and
        # the document says both rather than contradicting the gate.
        document = self._project(SUREFIRE, passed=True)

        self.assertEqual(Status.PASSED, document.status)
        self.assertEqual(1, document.metrics["failures"])
        self.assertEqual(2, len(document.findings))

    def test_a_case_a_feature_declares_twice_is_two_findings(self) -> None:
        raw = (
            b"<testsuite>"
            b"<testcase classname='c' name='Remove'><failure message='m'/></testcase>"
            b"<testcase classname='c' name='Remove'><failure message='m'/></testcase>"
            b"<testcase classname='c' name='Remove'><failure message='m'/></testcase>"
            b"</testsuite>"
        )

        document = self._project(raw)

        self.assertEqual(
            ["c.Remove", "c.Remove#2", "c.Remove#3"], [f.id for f in document.findings]
        )

    def test_a_case_without_a_class_is_named_by_its_name_alone(self) -> None:
        raw = b"<testsuite><testcase name='alone'><error/></testcase></testsuite>"

        [finding] = self._project(raw).findings

        self.assertEqual("alone", finding.id)
        self.assertEqual("error: error", finding.message)

    def test_no_report_says_so_and_counts_nothing(self) -> None:
        document = self._project(left=2)

        self.assertEqual(
            "no test report matching **/TEST-*.xml was written;"
            " 2 left from an earlier measurement, not read",
            document.summary,
        )
        self.assertEqual(
            {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "seconds": 0.0},
            document.metrics,
        )
        self.assertEqual((), document.findings)

    def test_renders_a_document_the_contract_accepts(self) -> None:
        for passed in (True, False):
            with self.subTest(passed=passed):
                raw = reports.render(self._project(SUREFIRE, passed=passed))

                self.assertEqual(("valid", None), classify(raw, passed=passed))
                document = json.loads(raw)
                self.assertEqual(1, document["schema_version"])
                self.assertEqual(
                    [
                        "findings",
                        "metrics",
                        "schema_version",
                        "sensor",
                        "status",
                        "summary",
                    ],
                    list(document),
                )
                self.assertTrue(raw.endswith(b"\n"))


if __name__ == "__main__":
    unittest.main()
