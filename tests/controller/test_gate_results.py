"""Telling a broken sensor from a failing candidate."""

import unittest

from codeservo.controller.gate_results import sensor_faults


class SensorFaultTests(unittest.TestCase):
    """A gate that cannot say what it measured is broken, and is said to be."""

    def _gate(
        self,
        name: str = "mutation",
        *,
        status: str | None = "valid",
        error: str | None = None,
        timed_out: bool = False,
        passed: bool = False,
    ) -> dict:
        record = {"name": name, "passed": passed, "timed_out": timed_out}
        if status is not None:
            record["observation_status"] = status
            record["observation_error"] = error
        return record

    def test_a_gate_answering_with_its_exit_code_alone_is_never_a_fault(self) -> None:
        self.assertEqual([], sensor_faults([self._gate(status=None)]))

    def test_a_valid_document_is_never_a_fault(self) -> None:
        self.assertEqual([], sensor_faults([self._gate()]))

    def test_names_the_fault_the_gate_and_the_sensor_error(self) -> None:
        for status in ("absent", "invalid", "contradicted"):
            with self.subTest(status=status):
                faults = sensor_faults(
                    [self._gate(status=status, error="field status is wrong")]
                )

                self.assertEqual(
                    ["sensor error: gate mutation: field status is wrong"], faults
                )

    def test_a_timeout_excuses_only_a_document_never_written(self) -> None:
        excused = sensor_faults(
            [self._gate(status="absent", error="wrote nothing", timed_out=True)]
        )
        judged = sensor_faults(
            [self._gate(status="invalid", error="field status", timed_out=True)]
        )

        self.assertEqual([], excused)
        self.assertEqual(["sensor error: gate mutation: field status"], judged)

    def test_reports_every_faulty_gate_of_a_phase(self) -> None:
        faults = sensor_faults(
            [
                self._gate("unit", passed=True),
                self._gate("mutation", status="absent", error="wrote nothing"),
                self._gate("runtime", status="invalid", error="field metrics"),
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
