"""Telling a broken sensor from a failing candidate."""

import unittest
from dataclasses import replace

from codeservo.controller.gate_results import sensor_faults
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


if __name__ == "__main__":
    unittest.main()
