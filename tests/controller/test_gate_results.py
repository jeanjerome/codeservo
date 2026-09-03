"""Telling a broken sensor from a failing candidate, and what to say about the latter."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from codeservo.controller.gate_results import (
    FINDINGS_FED_BACK,
    gate_clause,
    gate_feedback,
    sensor_faults,
)
from codeservo.domain.constitution import ResultFormat
from codeservo.sensors.gates import GateResult, UnsignedGateResult
from codeservo.sensors.observations import Classification


class SensorFaultTests(unittest.TestCase):
    """A gate that cannot say what it measured is broken, and is said to be."""

    def _gate(
        self,
        name: str = "mutation",
        *,
        status: Classification | None = Classification.VALID,
        error: str | None = None,
        timed_out: bool = False,
        passed: bool = False,
    ) -> GateResult:
        measured = UnsignedGateResult(
            name=name,
            command="true",
            exit_code=0 if passed else 1,
            timed_out=timed_out,
            duration_ms=1,
            stdout_path=f"{name}.stdout.log",
            stdout_sha256="",
            stderr_path=f"{name}.stderr.log",
            stderr_sha256="",
            result_format=(
                ResultFormat.EXIT_CODE
                if status is None
                else ResultFormat.CODESERVO_JSON
            ),
        )
        if status is None:
            return measured.signed()
        return replace(
            measured,
            observation_status=status,
            observation_error=error,
            observation_path=None,
            observation_sha256=None,
        ).signed()

    def test_a_gate_answering_with_its_exit_code_alone_is_never_a_fault(self) -> None:
        self.assertEqual([], sensor_faults([self._gate(status=None)]))

    def test_a_valid_document_is_never_a_fault(self) -> None:
        self.assertEqual([], sensor_faults([self._gate()]))

    def test_names_the_fault_the_gate_and_the_sensor_error(self) -> None:
        for status in (
            Classification.ABSENT,
            Classification.INVALID,
            Classification.CONTRADICTED,
        ):
            with self.subTest(status=status):
                faults = sensor_faults(
                    [self._gate(status=status, error="field status is wrong")]
                )

                self.assertEqual(
                    ["sensor error: gate mutation: field status is wrong"], faults
                )

    def test_a_timeout_excuses_only_a_document_never_written(self) -> None:
        excused = sensor_faults(
            [self._gate(status=Classification.ABSENT, error="wrote nothing", timed_out=True)]
        )
        judged = sensor_faults(
            [self._gate(status=Classification.INVALID, error="field status", timed_out=True)]
        )

        self.assertEqual([], excused)
        self.assertEqual(["sensor error: gate mutation: field status"], judged)

    def test_reports_every_faulty_gate_of_a_phase(self) -> None:
        faults = sensor_faults(
            [
                self._gate("unit", passed=True),
                self._gate("mutation", status=Classification.ABSENT, error="wrote nothing"),
                self._gate("runtime", status=Classification.INVALID, error="field metrics"),
            ]
        )

        self.assertEqual(
            [
                "sensor error: gate mutation: wrote nothing",
                "sensor error: gate runtime: field metrics",
            ],
            faults,
        )


DOCUMENT = {
    "schema_version": 1,
    "sensor": "unit",
    "status": "failed",
    "summary": "2 of 44 tests failed",
    "findings": [
        {
            "id": "tests/api/test_summary.py::test_counts",
            "severity": "major",
            "path": "tests/api/test_summary.py",
            "line": 82,
            "message": "assert 1 == 3",
        },
        {
            "id": "tests/api/test_summary.py::test_rate",
            "severity": "major",
            "path": "tests/api/test_summary.py",
            "line": None,
            "message": "ZeroDivisionError: division by zero",
        },
        {
            "id": "tree",
            "severity": "info",
            "path": None,
            "line": None,
            "message": "the suite ran on the candidate",
        },
    ],
    "metrics": {"tests": 44, "failures": 2, "duration_s": 7.25},
}


class GateFeedbackTests(unittest.TestCase):
    """A failing gate is told through its document first, then through its output."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.out = Path(self._temp.name)

    def _gate(
        self,
        name: str = "unit",
        *,
        document: dict | None = None,
        status: Classification = Classification.VALID,
        passed: bool = False,
    ) -> GateResult:
        stdout = self.out / f"{name}.stdout.log"
        stderr = self.out / f"{name}.stderr.log"
        stdout.write_text("FAILED test_counts - assert 1 == 3\n", encoding="utf-8")
        stderr.write_text("", encoding="utf-8")
        measured = UnsignedGateResult(
            name=name,
            command="pytest -q",
            exit_code=0 if passed else 1,
            timed_out=False,
            duration_ms=1,
            stdout_path=str(stdout),
            stdout_sha256="",
            stderr_path=str(stderr),
            stderr_sha256="",
            result_format=(
                ResultFormat.EXIT_CODE if document is None else ResultFormat.CODESERVO_JSON
            ),
        )
        if document is None:
            return measured.signed()
        kept = self.out / f"{name}.observation.json"
        kept.write_text(json.dumps(document), encoding="utf-8")
        return replace(
            measured,
            observation_status=status,
            observation_error=None,
            observation_path=str(kept),
            observation_sha256="",
        ).signed()

    def test_a_gate_answering_with_its_exit_code_alone_is_told_through_its_output(
        self,
    ) -> None:
        feedback = gate_feedback([self._gate()])

        self.assertEqual(
            "\n".join(
                [
                    "Gate unit FAILED",
                    "Command: pytest -q",
                    "Exit code: 1",
                    "stdout (tail):",
                    "FAILED test_counts - assert 1 == 3",
                    "stderr (tail):",
                    "",
                ]
            ),
            feedback,
        )

    def test_a_valid_document_comes_before_the_output(self) -> None:
        feedback = gate_feedback([self._gate(document=DOCUMENT)])

        self.assertEqual(
            "\n".join(
                [
                    "Gate unit FAILED",
                    "Command: pytest -q",
                    "Exit code: 1",
                    "Summary: 2 of 44 tests failed",
                    "Findings (3):",
                    "- [major] tests/api/test_summary.py:82: assert 1 == 3",
                    "- [major] tests/api/test_summary.py: ZeroDivisionError: division by zero",
                    "- [info] (no path): the suite ran on the candidate",
                    "Metrics: duration_s=7.25 failures=2 tests=44",
                    "stdout (tail):",
                    "FAILED test_counts - assert 1 == 3",
                    "stderr (tail):",
                    "",
                ]
            ),
            feedback,
        )

    def test_a_document_without_findings_or_metrics_says_so(self) -> None:
        document = {**DOCUMENT, "findings": [], "metrics": {}}

        feedback = gate_feedback([self._gate(document=document)])

        self.assertIn("Summary: 2 of 44 tests failed\nFindings: none\nstdout (tail):", feedback)
        self.assertNotIn("Metrics:", feedback)

    def test_findings_beyond_the_ceiling_are_counted_not_spelled_out(self) -> None:
        many = [
            {**DOCUMENT["findings"][0], "id": f"f{i}", "line": i + 1}
            for i in range(FINDINGS_FED_BACK + 5)
        ]

        feedback = gate_feedback([self._gate(document={**DOCUMENT, "findings": many})])

        self.assertIn(f"Findings ({FINDINGS_FED_BACK + 5}):", feedback)
        self.assertIn(f"tests/api/test_summary.py:{FINDINGS_FED_BACK}: assert", feedback)
        self.assertNotIn(f"tests/api/test_summary.py:{FINDINGS_FED_BACK + 1}: assert", feedback)
        self.assertIn("- ... and 5 more", feedback)

    def test_a_document_that_is_not_valid_is_not_read(self) -> None:
        feedback = gate_feedback(
            [self._gate(document=DOCUMENT, status=Classification.CONTRADICTED)]
        )

        self.assertNotIn("Summary:", feedback)
        self.assertIn("stdout (tail):", feedback)

    def test_a_clause_names_the_gate_and_what_its_document_summarised(self) -> None:
        self.assertEqual(
            "unit (2 of 44 tests failed)", gate_clause(self._gate(document=DOCUMENT))
        )

    def test_a_clause_falls_back_on_how_the_gate_ended(self) -> None:
        self.assertEqual("unit (exit code 1)", gate_clause(self._gate()))
        self.assertEqual(
            "unit (exit code 1)",
            gate_clause(self._gate(document={**DOCUMENT, "summary": ""})),
        )
        timed_out = replace(self._gate(), exit_code=None, timed_out=True)
        self.assertEqual("unit (timed out)", gate_clause(timed_out))

    def test_a_passing_gate_is_not_mentioned(self) -> None:
        feedback = gate_feedback(
            [self._gate("lint", passed=True), self._gate("unit", document=DOCUMENT)]
        )

        self.assertNotIn("Gate lint", feedback)
        self.assertTrue(feedback.startswith("Gate unit FAILED"))


if __name__ == "__main__":
    unittest.main()
