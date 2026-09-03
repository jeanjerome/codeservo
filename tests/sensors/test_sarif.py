"""Reading SARIF analysis results, and projecting them onto the observation."""

import json
import tempfile
import unittest
from pathlib import Path

from codeservo.sensors import reports, sarif
from codeservo.sensors.observations import Severity, Status, classify


def sarif_log(*runs: dict, version: str = sarif.VERSION) -> bytes:
    """One SARIF log holding the runs a test describes."""
    return json.dumps(
        {
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "version": version,
            "runs": list(runs),
        }
    ).encode("utf-8")


def run_of(
    *results: dict, rules: list | None = None, tool: str = "ruff", **extra
) -> dict:
    driver: dict = {"name": tool, "version": "0.12.12"}
    if rules is not None:
        driver["rules"] = rules
    return {"tool": {"driver": driver}, "results": list(results), **extra}


def result_at(uri: str, *, line: int = 1, rule: str = "F401", **extra) -> dict:
    """One result shaped the way ruff writes it, an absolute file URI included."""
    return {
        "ruleId": rule,
        "level": "error",
        "message": {"text": "`os` imported but unused"},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": uri},
                    "region": {
                        "startLine": line,
                        "startColumn": 1,
                        "endLine": line,
                        "endColumn": 1,
                    },
                }
            }
        ],
        **extra,
    }


# What ruff 0.12.12 wrote on a tree with two faulty files, trimmed to two
# results and one rule and otherwise field for field as it came out.
RUFF_RULE = {
    "id": "F401",
    "shortDescription": {"text": "`{name}` imported but unused"},
    "fullDescription": {"text": "## What it does\nChecks for unused imports.\n"},
    "help": {"text": "`{name}` imported but unused"},
    "helpUri": "https://docs.astral.sh/ruff/rules/unused-import",
    "properties": {"id": "F401", "kind": "Pyflakes"},
}


class RuffTests(unittest.TestCase):
    """The one producer this reader was measured against."""

    def _tree(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        tree = Path(temp.name)
        (tree / "sub").mkdir()
        return tree

    def test_reads_the_shape_ruff_writes(self) -> None:
        tree = self._tree()
        raw = sarif_log(
            run_of(
                result_at(f"file://{tree / 'bad.py'}", line=1),
                result_at(f"file://{tree / 'sub' / 'other.py'}", line=4, rule="S110"),
                rules=[RUFF_RULE],
            )
        )

        report = sarif.parse_report(raw, "lint.sarif", tree=tree)

        self.assertEqual(("ruff 0.12.12",), report.tools)
        self.assertEqual(0, report.suppressed)
        self.assertEqual(
            [
                ("F401", sarif.Level.ERROR, "bad.py", 1),
                ("S110", sarif.Level.ERROR, "sub/other.py", 4),
            ],
            [(r.rule, r.level, r.path, r.line) for r in report.results],
        )
        self.assertEqual("`os` imported but unused", report.results[0].message)

    def test_a_clean_tree_is_a_document_counting_nothing(self) -> None:
        # Measured: ruff writes a valid log with an empty results array and
        # exits zero, so a passing gate does leave a report.
        tree = self._tree()
        raw = sarif_log(run_of(rules=[]))

        report = sarif.parse_report(raw, "lint.sarif", tree=tree)
        document = sarif.project(
            [report], sensor="lint", passed=True, pattern="*.sarif", left=0
        )

        self.assertEqual((), report.results)
        self.assertEqual(Status.PASSED, document.status)
        self.assertEqual(
            "0 results, 0 errors, 0 warnings, 0 notes from ruff 0.12.12 in 1 report",
            document.summary,
        )
        self.assertEqual(0, document.metrics["results"])


class LevelTests(unittest.TestCase):
    """How severe a result is, when it does not say."""

    TREE = Path("/tree")

    def _level(self, result: dict, rules: list | None = None) -> sarif.Level:
        raw = sarif_log(run_of(result, rules=rules))
        [read] = sarif.parse_report(raw, "r.sarif", tree=self.TREE).results
        return read.level

    def test_a_result_naming_its_level_carries_it(self) -> None:
        for declared in sarif.Level:
            with self.subTest(declared):
                self.assertEqual(
                    declared, self._level({"ruleId": "R", "level": str(declared)})
                )

    def test_a_result_without_one_takes_its_rule_default(self) -> None:
        rules = [{"id": "R", "defaultConfiguration": {"level": "note"}}]

        self.assertEqual(sarif.Level.NOTE, self._level({"ruleId": "R"}, rules))
        # By index as well as by identifier, the index being authoritative.
        self.assertEqual(
            sarif.Level.NOTE, self._level({"ruleIndex": 0, "ruleId": "other"}, rules)
        )

    def test_a_result_whose_rule_says_nothing_takes_the_defined_default(self) -> None:
        self.assertEqual("warning", sarif.DEFAULT_LEVEL)
        for rules in (
            None,
            [],
            [{"id": "R"}],
            [{"id": "R", "defaultConfiguration": {}}],
        ):
            with self.subTest(rules=rules):
                self.assertEqual(
                    sarif.Level.WARNING, self._level({"ruleId": "R"}, rules)
                )

    def test_a_level_no_version_defines_is_read_as_the_default(self) -> None:
        self.assertEqual(
            sarif.Level.WARNING, self._level({"ruleId": "R", "level": "critical"})
        )

    def test_an_index_naming_no_rule_falls_back_to_the_identifier(self) -> None:
        rules = [{"id": "R", "defaultConfiguration": {"level": "note"}}]

        for index in (7, -1, "0", True):
            with self.subTest(index=index):
                self.assertEqual(
                    sarif.Level.NOTE,
                    self._level({"ruleIndex": index, "ruleId": "R"}, rules),
                )


class CountedTests(unittest.TestCase):
    """Which results are failures the tool stands behind."""

    TREE = Path("/tree")

    def _report(self, *results: dict) -> sarif.Report:
        return sarif.parse_report(
            sarif_log(run_of(*results)), "r.sarif", tree=self.TREE
        )

    def test_a_result_that_is_not_a_failure_is_not_counted(self) -> None:
        report = self._report(
            {"ruleId": "A", "kind": "pass"},
            {"ruleId": "B", "kind": "informational"},
            {"ruleId": "C", "kind": "fail"},
            {"ruleId": "D"},
        )

        self.assertEqual(["C", "D"], [result.rule for result in report.results])
        self.assertEqual(0, report.suppressed)

    def test_a_suppressed_result_is_counted_and_is_no_finding(self) -> None:
        report = self._report(
            {"ruleId": "A", "suppressions": [{"kind": "inSource"}]},
            {
                "ruleId": "B",
                "suppressions": [{"kind": "external", "status": "accepted"}],
            },
            {"ruleId": "C"},
        )

        self.assertEqual(["C"], [result.rule for result in report.results])
        self.assertEqual(2, report.suppressed)

    def test_a_suppression_the_tool_rejected_suppresses_nothing(self) -> None:
        report = self._report(
            {
                "ruleId": "A",
                "suppressions": [{"kind": "inSource", "status": "rejected"}],
            },
            {"ruleId": "B", "suppressions": []},
        )

        self.assertEqual(["A", "B"], [result.rule for result in report.results])
        self.assertEqual(0, report.suppressed)

    def test_what_names_the_rule_a_result_fired(self) -> None:
        rules = [{"id": "FROM-INDEX"}]

        report = sarif.parse_report(
            sarif_log(
                run_of(
                    {"ruleId": "FROM-RESULT"},
                    {"ruleIndex": 0},
                    {},
                    rules=rules,
                )
            ),
            "r.sarif",
            tree=self.TREE,
        )

        self.assertEqual(
            ["FROM-RESULT", "FROM-INDEX", "result"],
            [result.rule for result in report.results],
        )

    def test_what_the_tool_said_about_one_result(self) -> None:
        rules = [{"id": "R", "shortDescription": {"text": "from the rule"}}]

        report = sarif.parse_report(
            sarif_log(
                run_of(
                    {"ruleId": "R", "message": {"text": " first\nsecond "}},
                    {"ruleId": "R", "message": {"markdown": "**bold**"}},
                    {"ruleId": "R", "message": {}},
                    {"ruleId": "R", "message": {"text": 7}},
                    {"ruleId": "Q"},
                    rules=rules,
                )
            ),
            "r.sarif",
            tree=self.TREE,
        )

        self.assertEqual(
            ["first", "**bold**", "from the rule", "from the rule", "no message"],
            [result.message for result in report.results],
        )


class LocationTests(unittest.TestCase):
    """Where a result points, read against the tree the gate measured."""

    def _path(self, uri: str, *, tree: Path, region: dict | None = None) -> tuple:
        location = {
            "physicalLocation": {
                "artifactLocation": {"uri": uri},
                "region": {"startLine": 3} if region is None else region,
            }
        }
        raw = sarif_log(run_of({"ruleId": "R", "locations": [location]}))
        [result] = sarif.parse_report(raw, "r.sarif", tree=tree).results
        return result.path, result.line

    def test_an_absolute_file_uri_inside_the_tree_is_relativised(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp)

            self.assertEqual(
                ("sub/app.py", 3),
                self._path(f"file://{tree / 'sub' / 'app.py'}", tree=tree),
            )

    def test_a_percent_encoded_uri_is_decoded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp)
            encoded = str(tree / "with space.py").replace(" ", "%20")

            self.assertEqual(
                ("with space.py", 3), self._path(f"file://{encoded}", tree=tree)
            )

    def test_a_uri_outside_the_tree_points_nowhere_and_names_no_line(self) -> None:
        # A line number without a file is a line of nowhere, so the location
        # the reader could not place carries neither.
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp) / "tree"
            tree.mkdir()

            for uri in (
                f"file://{Path(temp) / 'elsewhere.py'}",
                "file:///etc/passwd",
                "file://remote-host/a.py",
                "https://example.test/a.py",
                "../outside.py",
                "  ",
            ):
                with self.subTest(uri):
                    self.assertEqual((None, None), self._path(uri, tree=tree))

    def test_a_relative_uri_is_already_what_the_record_wants(self) -> None:
        self.assertEqual(
            ("src/app.py", 3), self._path("src/app.py", tree=Path("/tree"))
        )
        self.assertEqual(
            ("src/app.py", 3), self._path("file:src/app.py", tree=Path("/tree"))
        )

    def test_a_start_line_that_is_not_a_positive_integer_is_no_line(self) -> None:
        for region in ({}, {"startLine": 0}, {"startLine": "3"}, {"startLine": True}):
            with self.subTest(region=region):
                self.assertEqual(
                    ("a.py", None),
                    self._path("a.py", tree=Path("/tree"), region=region),
                )

    def test_the_first_location_naming_a_file_of_the_tree_is_the_one(self) -> None:
        raw = sarif_log(
            run_of(
                {
                    "ruleId": "R",
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": "/outside.py"}
                            }
                        },
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": "inside.py"},
                                "region": {"startLine": 9},
                            }
                        },
                    ],
                }
            )
        )

        [result] = sarif.parse_report(raw, "r.sarif", tree=Path("/tree")).results

        self.assertEqual(("inside.py", 9), (result.path, result.line))

    def test_a_result_naming_no_location_points_nowhere(self) -> None:
        for result in ({"ruleId": "R"}, {"ruleId": "R", "locations": []}):
            with self.subTest(result=result):
                raw = sarif_log(run_of(result))

                [read] = sarif.parse_report(raw, "r.sarif", tree=Path("/tree")).results

                self.assertEqual((None, None), (read.path, read.line))


class RefusalTests(unittest.TestCase):
    """What a file matching the pattern is not, named rather than guessed at."""

    TREE = Path("/tree")

    def _refused(self, raw: bytes) -> str:
        with self.assertRaises(reports.ReportFault) as refused:
            sarif.parse_report(raw, "r.sarif", tree=self.TREE)
        return str(refused.exception)

    def test_refuses_a_run_its_own_tool_says_did_not_complete(self) -> None:
        # The reading that matters: an unfinished tool writes the same empty
        # results array as a clean tree.
        raw = sarif_log(run_of(invocations=[{"executionSuccessful": False}]))

        self.assertIn("reports a tool run that did not complete", self._refused(raw))

    def test_a_run_that_completed_is_read(self) -> None:
        raw = sarif_log(
            run_of(
                {"ruleId": "R"},
                invocations=[
                    {"executionSuccessful": True},
                    {"exitCode": 1},
                ],
            )
        )

        report = sarif.parse_report(raw, "r.sarif", tree=self.TREE)

        self.assertEqual(1, len(report.results))

    def test_refuses_a_version_this_reader_was_not_measured_against(self) -> None:
        self.assertEqual("2.1.0", sarif.VERSION)
        for version, named in (("2.2.0", "2.2.0"), ("", "none"), ("1.0.0", "1.0.0")):
            with self.subTest(version):
                wrong = self._refused(sarif_log(run_of(), version=version))

                self.assertIn(f"declares SARIF version {named}", wrong)
                self.assertIn("measured against 2.1.0", wrong)

    def test_refuses_what_is_not_a_sarif_log(self) -> None:
        for raw, wrong in (
            (b"{", "r.sarif is not JSON"),
            (b"[]", "it is not a JSON object"),
            (b'{"version": "2.1.0"}', "it names no array of runs"),
            (b'{"version": "2.1.0", "runs": {}}', "it names no array of runs"),
            (b'\xff\xfe{"version": "2.1.0"}', "r.sarif is not valid UTF-8"),
            (b'{"version": "2.1.0", "runs": [], "x": NaN}', "NaN is not a JSON value"),
        ):
            with self.subTest(wrong):
                self.assertIn(wrong, self._refused(raw))

    def test_refuses_an_array_that_decides_a_count_and_holds_something_else(
        self,
    ) -> None:
        # Reading past one of these would report a count nothing measured.
        for runs, wrong in (
            ([7], "names a run that is not an object: runs[0]"),
            (
                [{"results": "many"}],
                "names results that are not an array: runs[0].results",
            ),
            (
                [{"results": [{"ruleId": "A"}, 4]}],
                "names a result that is not an object: runs[0].results[1]",
            ),
            (
                [{"results": []}, {"results": [None]}],
                "names a result that is not an object: runs[1].results[0]",
            ),
        ):
            with self.subTest(wrong):
                raw = json.dumps({"version": "2.1.0", "runs": runs}).encode()

                self.assertIn(wrong, self._refused(raw))

    def test_a_run_reporting_no_results_found_nothing(self) -> None:
        for run in ({}, {"results": []}, {"tool": {"driver": {"name": "ruff"}}}):
            with self.subTest(run=run):
                raw = json.dumps({"version": "2.1.0", "runs": [run]}).encode()

                report = sarif.parse_report(raw, "r.sarif", tree=self.TREE)

                self.assertEqual((), report.results)

    def test_a_wrongly_typed_field_of_a_result_is_a_value_it_does_not_have(
        self,
    ) -> None:
        # A field inside a result decorates that one finding, so a wrong type
        # leaves the finding standing rather than ending the run.
        raw = json.dumps(
            {
                "version": "2.1.0",
                "runs": [
                    {"tool": 7, "results": [{"ruleId": "A"}]},
                    {
                        "tool": {"driver": {"name": 3, "rules": 5}},
                        "results": [
                            {"ruleId": None, "locations": "here", "message": 2},
                            {"ruleId": "B", "level": 9, "suppressions": "none"},
                        ],
                    },
                ],
            }
        ).encode()

        report = sarif.parse_report(raw, "r.sarif", tree=self.TREE)

        self.assertEqual((), report.tools)
        self.assertEqual(
            [
                ("A", sarif.Level.WARNING, None, None),
                ("result", sarif.Level.WARNING, None, None),
                ("B", sarif.Level.WARNING, None, None),
            ],
            [(r.rule, r.level, r.path, r.line) for r in report.results],
        )
        self.assertEqual(0, report.suppressed)


class ProjectionTests(unittest.TestCase):
    """The reports, as the document every reader of an observation expects."""

    TREE = Path("/tree")

    def _project(self, *runs: dict, passed: bool = False, left: int = 0):
        report = sarif.parse_report(sarif_log(*runs), "r.sarif", tree=self.TREE)
        return sarif.project(
            [report], sensor="lint", passed=passed, pattern="**/*.sarif", left=left
        )

    def test_counts_every_level_and_names_every_result(self) -> None:
        document = self._project(
            run_of(
                {"ruleId": "A", "level": "error", "message": {"text": "boom"}},
                {
                    "ruleId": "B",
                    "level": "warning",
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": "a.py"},
                                "region": {"startLine": 2},
                            }
                        }
                    ],
                },
                {"ruleId": "C", "level": "note"},
                {"ruleId": "D", "level": "none"},
            )
        )

        self.assertEqual(
            {"results": 4, "errors": 1, "warnings": 1, "notes": 1, "suppressed": 0},
            document.metrics,
        )
        self.assertEqual(
            "4 results, 1 errors, 1 warnings, 1 notes from ruff 0.12.12 in 1 report",
            document.summary,
        )
        self.assertEqual(
            [
                ("A", Severity.MAJOR, None, None, "error: boom"),
                ("B:a.py", Severity.MINOR, "a.py", 2, "warning: no message"),
                ("C", Severity.INFO, None, None, "note: no message"),
                ("D", Severity.INFO, None, None, "none: no message"),
            ],
            [(f.id, f.severity, f.path, f.line, f.message) for f in document.findings],
        )

    def test_a_result_with_no_severity_is_in_the_total_and_under_no_level(self) -> None:
        document = self._project(run_of({"ruleId": "D", "level": "none"}))

        counts = document.metrics
        self.assertEqual(1, counts["results"])
        self.assertEqual(0, counts["errors"] + counts["warnings"] + counts["notes"])

    def test_the_status_is_the_verdict_the_exit_code_reached(self) -> None:
        # A tool told to report without failing passes with results on record.
        document = self._project(run_of({"ruleId": "A", "level": "error"}), passed=True)

        self.assertEqual(Status.PASSED, document.status)
        self.assertEqual(1, document.metrics["errors"])
        self.assertEqual(1, len(document.findings))

    def test_one_rule_firing_twice_in_one_file_is_two_findings(self) -> None:
        location = {"physicalLocation": {"artifactLocation": {"uri": "a.py"}}}
        document = self._project(
            run_of(*[{"ruleId": "F401", "locations": [location]} for _ in range(3)])
        )

        self.assertEqual(
            ["F401:a.py", "F401:a.py#2", "F401:a.py#3"],
            [finding.id for finding in document.findings],
        )

    def test_several_runs_and_several_tools_are_one_document(self) -> None:
        document = self._project(
            run_of({"ruleId": "A", "level": "error"}, tool="ruff"),
            run_of({"ruleId": "B", "level": "warning"}, tool="semgrep"),
        )

        self.assertEqual(2, document.metrics["results"])
        self.assertIn("from ruff 0.12.12, semgrep 0.12.12", document.summary)

    def test_says_what_it_suppressed_and_what_it_did_not_read(self) -> None:
        document = self._project(
            run_of(
                {"ruleId": "A", "level": "error"},
                {"ruleId": "B", "suppressions": [{"kind": "inSource"}]},
            ),
            left=2,
        )

        self.assertEqual(
            "1 results, 1 errors, 0 warnings, 0 notes from ruff 0.12.12"
            " in 1 report, 1 suppressed;"
            " 2 left from an earlier measurement, not read",
            document.summary,
        )
        self.assertEqual(1, document.metrics["suppressed"])

    def test_no_report_says_so_and_counts_nothing(self) -> None:
        document = sarif.project(
            [], sensor="lint", passed=False, pattern="**/*.sarif", left=0
        )

        self.assertEqual(
            "no analysis report matching **/*.sarif was written", document.summary
        )
        self.assertEqual(
            {"results": 0, "errors": 0, "warnings": 0, "notes": 0, "suppressed": 0},
            document.metrics,
        )
        self.assertEqual((), document.findings)

    def test_renders_a_document_the_contract_accepts(self) -> None:
        for passed in (True, False):
            with self.subTest(passed=passed):
                document = self._project(
                    run_of({"ruleId": "A", "level": "error"}), passed=passed
                )

                raw = reports.render(document)

                self.assertEqual(("valid", None), classify(raw, passed=passed))


class ProjectionThroughTheTreeTests(unittest.TestCase):
    """Reading the files a measurement wrote, and projecting them as one."""

    def test_reads_every_report_the_measurement_wrote(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp)
            (tree / "one.sarif").write_bytes(
                sarif_log(run_of({"ruleId": "A", "level": "error"}))
            )
            (tree / "two.sarif").write_bytes(
                sarif_log(run_of({"ruleId": "B", "level": "warning"}, tool="mypy"))
            )

            document = sarif.projection(
                tree,
                ["one.sarif", "two.sarif"],
                sensor="lint",
                passed=False,
                pattern="*.sarif",
                left=0,
            )

            self.assertEqual(2, document.metrics["results"])
            self.assertIn("in 2 reports", document.summary)

    def test_one_unreadable_report_refuses_the_whole_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp)
            (tree / "one.sarif").write_bytes(sarif_log(run_of()))
            (tree / "two.sarif").write_bytes(b"not json")

            with self.assertRaisesRegex(reports.ReportFault, "two.sarif is not JSON"):
                sarif.projection(
                    tree,
                    ["one.sarif", "two.sarif"],
                    sensor="lint",
                    passed=True,
                    pattern="*.sarif",
                    left=0,
                )


if __name__ == "__main__":
    unittest.main()
