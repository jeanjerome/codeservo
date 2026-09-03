"""What a later iteration is told about the iterations before it."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from codeservo.controller.document import FileRecord, Iteration, ScopeResult
from codeservo.controller.phases.iteration import iteration_recap
from codeservo.domain.constitution import ResultFormat
from codeservo.sensors.gates import GateResult, UnsignedGateResult
from codeservo.sensors.observations import Classification

SNAPSHOT = FileRecord(path="input.patch", sha256="0" * 64)


class IterationRecapTests(unittest.TestCase):
    """One line per iteration, naming each failing gate with what it said."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.out = Path(self._temp.name)

    def _gate(self, name: str, *, passed: bool, summary: str | None = None) -> GateResult:
        # Each measurement keeps its own document, as each iteration's does.
        self.count = getattr(self, "count", 0) + 1
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
                ResultFormat.EXIT_CODE if summary is None else ResultFormat.CODESERVO_JSON
            ),
        )
        if summary is None:
            return measured.signed()
        kept = self.out / f"{self.count:02d}-{name}.observation.json"
        kept.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "sensor": name,
                    "status": "passed" if passed else "failed",
                    "summary": summary,
                    "findings": [],
                    "metrics": {},
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

    def _iteration(
        self, number: int, *, scope: ScopeResult, gates: tuple[GateResult, ...]
    ) -> Iteration:
        return Iteration(
            iteration=number,
            feedback_received="",
            input_state=SNAPSHOT,
            scope=scope,
            quick_gates=gates,
        )

    def test_nothing_to_tell_before_the_first_iteration(self) -> None:
        self.assertEqual((), iteration_recap(()))

    def test_one_line_per_iteration_in_order(self) -> None:
        ok = ScopeResult(passed=True, summary="scope OK", details={})
        first = self._iteration(
            1,
            scope=ok,
            gates=(
                self._gate("lint", passed=True),
                self._gate("unit", passed=False, summary="2 of 44 tests failed"),
                self._gate("contract", passed=False),
            ),
        )
        second = self._iteration(
            2,
            scope=ScopeResult(
                passed=False, summary="changed files 15 exceed max 12", details={}
            ),
            gates=(
                self._gate("lint", passed=True),
                self._gate("unit", passed=False, summary="1 of 44 tests failed"),
                self._gate("contract", passed=True),
            ),
        )

        recap = iteration_recap((first, second))

        self.assertEqual(
            (
                "Iteration 1: scope OK; quick gates: 1 of 3 passed;"
                " failed: unit (2 of 44 tests failed), contract (exit code 1)",
                "Iteration 2: changed files 15 exceed max 12; quick gates: 2 of 3 passed;"
                " failed: unit (1 of 44 tests failed)",
            ),
            recap,
        )

    def test_an_iteration_the_loop_never_measured_is_said_to_be_unmeasured(
        self,
    ) -> None:
        unmeasured = Iteration(iteration=1, feedback_received="", input_state=SNAPSHOT)

        self.assertEqual(("Iteration 1: not measured",), iteration_recap((unmeasured,)))


if __name__ == "__main__":
    unittest.main()
