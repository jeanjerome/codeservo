"""A passing gate held to what it reported at the baseline."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from codeservo.controller.gate_results import GatePhase
from codeservo.controller.ratchet import (
    BrokenRatchet,
    broken_ratchets,
    holds,
    ratchet_clause,
    ratchet_feedback,
    ratchet_reasons,
)
from codeservo.domain.constitution import Direction, Gate, Ratchet, ResultFormat
from codeservo.sensors.gates import GateResult, UnsignedGateResult
from codeservo.sensors.observations import Classification

COVERAGE = Gate(
    name="coverage",
    phase="full",
    command="make coverage",
    result_format=ResultFormat.CODESERVO_JSON,
    ratchets=(
        Ratchet(metric="line_coverage", direction=Direction.AT_LEAST),
        Ratchet(metric="missing", direction=Direction.AT_MOST),
    ),
)
UNIT = Gate(
    name="unit",
    phase="quick",
    command="make test",
    result_format=ResultFormat.CODESERVO_JSON,
    ratchets=(Ratchet(metric="failures", direction=Direction.AT_MOST),),
)
LINT = Gate(name="lint", phase="quick", command="make lint")


class HoldsTests(unittest.TestCase):
    def test_each_direction_lets_a_value_move_one_way_and_stay(self) -> None:
        cases = (
            (Direction.AT_MOST, 12, 11, True),
            (Direction.AT_MOST, 12, 12, True),
            (Direction.AT_MOST, 12, 13, False),
            (Direction.AT_LEAST, 94.9, 95.0, True),
            (Direction.AT_LEAST, 94.9, 94.9, True),
            (Direction.AT_LEAST, 94.9, 94.8, False),
        )
        for direction, baseline, candidate, expected in cases:
            with self.subTest(direction=direction, baseline=baseline, candidate=candidate):
                self.assertEqual(expected, holds(direction, baseline, candidate))


class BrokenRatchetTests(unittest.TestCase):
    """Which declared ratchets a candidate broke, read off two documents."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.out = Path(self._temp.name)
        self.count = 0

    def _result(
        self,
        name: str,
        metrics: dict | None = None,
        *,
        passed: bool = True,
    ) -> GateResult:
        """One gate result, with a valid document when metrics are given."""
        self.count += 1
        measured = UnsignedGateResult(
            name=name,
            command="make " + name,
            exit_code=0 if passed else 1,
            timed_out=False,
            duration_ms=1,
            stdout_path=str(self.out / f"{name}.stdout.log"),
            stdout_sha256="",
            stderr_path=str(self.out / f"{name}.stderr.log"),
            stderr_sha256="",
            result_format=(
                ResultFormat.EXIT_CODE if metrics is None else ResultFormat.CODESERVO_JSON
            ),
        )
        if metrics is None:
            return measured.signed()
        kept = self.out / f"{self.count:02d}-{name}.observation.json"
        kept.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "sensor": name,
                    "status": "passed" if passed else "failed",
                    "summary": name,
                    "findings": [],
                    "metrics": metrics,
                }
            ),
            encoding="utf-8",
        )
        return replace(
            measured,
            observation_status=Classification.VALID,
            observation_error=None,
            observation_path=str(kept),
            observation_sha256="",
        ).signed()

    def test_a_metric_that_moved_the_wrong_way_is_broken_with_both_values(self) -> None:
        baseline = [self._result("coverage", {"line_coverage": 94.91, "missing": 12})]
        candidate = [self._result("coverage", {"line_coverage": 93.8, "missing": 15})]

        self.assertEqual(
            [
                BrokenRatchet(
                    gate="coverage",
                    metric="line_coverage",
                    direction=Direction.AT_LEAST,
                    baseline=94.91,
                    candidate=93.8,
                ),
                BrokenRatchet(
                    gate="coverage",
                    metric="missing",
                    direction=Direction.AT_MOST,
                    baseline=12,
                    candidate=15,
                ),
            ],
            broken_ratchets([COVERAGE], baseline, candidate),
        )

    def test_a_metric_that_moved_the_right_way_or_stayed_holds(self) -> None:
        baseline = [self._result("coverage", {"line_coverage": 94.91, "missing": 12})]
        candidate = [self._result("coverage", {"line_coverage": 95.2, "missing": 12})]

        self.assertEqual([], broken_ratchets([COVERAGE], baseline, candidate))

    def test_silent_when_either_document_lacks_the_metric(self) -> None:
        cases = (
            ("candidate", {"missing": 12}, {"line_coverage": 50.0}),
            ("baseline", {"line_coverage": 50.0}, {"missing": 40}),
            ("both", {}, {}),
        )
        for lacking, before, after in cases:
            with self.subTest(lacking=lacking):
                baseline = [self._result("coverage", before)]
                candidate = [self._result("coverage", after)]

                self.assertEqual([], broken_ratchets([COVERAGE], baseline, candidate))

    def test_silent_when_the_baseline_never_measured_the_gate(self) -> None:
        candidate = [self._result("coverage", {"line_coverage": 1.0, "missing": 99})]

        self.assertEqual([], broken_ratchets([COVERAGE], [], candidate))

    def test_a_failing_gate_is_not_compared(self) -> None:
        """It already decided against the candidate, over different work."""
        baseline = [self._result("unit", {"failures": 0})]
        candidate = [self._result("unit", {"failures": 3}, passed=False)]

        self.assertEqual([], broken_ratchets([UNIT], baseline, candidate))

    def test_a_gate_answering_with_its_exit_code_alone_is_not_compared(self) -> None:
        baseline = [self._result("unit", {"failures": 0})]
        candidate = [self._result("unit")]

        self.assertEqual([], broken_ratchets([UNIT], baseline, candidate))

    def test_a_gate_declaring_no_ratchet_is_not_read(self) -> None:
        baseline = [self._result("lint", {"violations": 0})]
        candidate = [self._result("lint", {"violations": 7})]

        self.assertEqual([], broken_ratchets([LINT], baseline, candidate))

    def test_the_order_is_the_constitutions_then_the_declarations(self) -> None:
        baseline = [
            self._result("coverage", {"line_coverage": 90.0, "missing": 1}),
            self._result("unit", {"failures": 0}),
        ]
        # Measured in another order than declared.
        candidate = [
            self._result("unit", {"failures": 1}),
            self._result("coverage", {"line_coverage": 80.0, "missing": 2}),
        ]

        broken = broken_ratchets([UNIT, COVERAGE], baseline, candidate)

        self.assertEqual(
            [("unit", "failures"), ("coverage", "line_coverage"), ("coverage", "missing")],
            [(item.gate, item.metric) for item in broken],
        )


class RatchetWordingTests(unittest.TestCase):
    """What the record and the actuator are told about a broken ratchet."""

    BROKEN = (
        BrokenRatchet(
            gate="coverage",
            metric="missing",
            direction=Direction.AT_MOST,
            baseline=12,
            candidate=15,
        ),
        BrokenRatchet(
            gate="coverage",
            metric="line_coverage",
            direction=Direction.AT_LEAST,
            baseline=94.91,
            candidate=93.8,
        ),
        BrokenRatchet(
            gate="unit",
            metric="failures",
            direction=Direction.AT_MOST,
            baseline=0,
            candidate=1,
        ),
    )

    def test_a_reason_names_the_phase_the_gate_the_metric_and_both_values(self) -> None:
        self.assertEqual(
            [
                "full gate coverage ratchet broken: missing 15 on the candidate,"
                " 12 on the baseline, must be <=",
                "full gate coverage ratchet broken: line_coverage 93.8 on the"
                " candidate, 94.91 on the baseline, must be >=",
                "full gate unit ratchet broken: failures 1 on the candidate,"
                " 0 on the baseline, must be <=",
            ],
            ratchet_reasons(GatePhase.FULL, self.BROKEN),
        )

    def test_the_feedback_groups_what_broke_by_gate(self) -> None:
        self.assertEqual(
            "\n".join(
                [
                    "Gate coverage passed but broke a ratchet",
                    "- missing 15 on the candidate, 12 on the baseline, must be <=",
                    "- line_coverage 93.8 on the candidate, 94.91 on the baseline,"
                    " must be >=",
                    "",
                    "Gate unit passed but broke a ratchet",
                    "- failures 1 on the candidate, 0 on the baseline, must be <=",
                ]
            ),
            ratchet_feedback(self.BROKEN),
        )

    def test_nothing_broken_says_nothing(self) -> None:
        self.assertEqual("", ratchet_feedback([]))
        self.assertEqual([], ratchet_reasons(GatePhase.QUICK, []))

    def test_the_recap_clause_carries_each_gate_metric_and_both_values(self) -> None:
        self.assertEqual(
            "ratchet broken: coverage missing 15 vs 12,"
            " coverage line_coverage 93.8 vs 94.91, unit failures 1 vs 0",
            ratchet_clause(self.BROKEN),
        )


if __name__ == "__main__":
    unittest.main()
