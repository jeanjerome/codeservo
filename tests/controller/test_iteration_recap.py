"""What a later iteration is told about the iterations before it."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from codeservo.controller.document import (
    FileRecord,
    IsolationEvidence,
    Iteration,
    ReviewBlock,
    ScopeResult,
)
from codeservo.controller.phases.iteration import iteration_recap
from codeservo.domain.constitution import ResultFormat
from codeservo.sensors.gates import GateResult, UnsignedGateResult
from codeservo.sensors.observations import Classification

SNAPSHOT = FileRecord(path="input.patch", sha256="0" * 64)
CRITERIA = {"AC1": "one", "AC2": "two"}
BLOCKING = ("blocker", "major")
OK = ScopeResult(passed=True, summary="scope OK", details={})


def recap(iterations: tuple[Iteration, ...]) -> tuple[str, ...]:
    return iteration_recap(iterations, CRITERIA, BLOCKING)


def review_block(result: dict | None) -> ReviewBlock:
    block = ReviewBlock(
        prompt=FileRecord(path="review/prompt.md", sha256="1" * 64),
        observations={"schema_version": 1, "gates": []},
        observations_sha256="2" * 64,
        isolation=IsolationEvidence(
            mechanism="none",
            denied_paths=(),
            read_only_paths=(),
            user_config_ignored=True,
        ),
    )
    if result is None:
        return block
    return replace(block, result=result, result_sha256="3" * 64)


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
        self,
        number: int,
        *,
        scope: ScopeResult = OK,
        gates: tuple[GateResult, ...],
        **rest,
    ) -> Iteration:
        return Iteration(
            iteration=number,
            feedback_received="",
            input_state=SNAPSHOT,
            scope=scope,
            quick_gates=gates,
            **rest,
        )

    def test_nothing_to_tell_before_the_first_iteration(self) -> None:
        self.assertEqual((), recap(()))

    def test_one_line_per_iteration_in_order(self) -> None:
        ok = OK
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

        self.assertEqual(
            (
                "Iteration 1: scope OK; quick gates: 1 of 3 passed;"
                " failed: unit (2 of 44 tests failed), contract (exit code 1)",
                "Iteration 2: changed files 15 exceed max 12; quick gates: 2 of 3 passed;"
                " failed: unit (1 of 44 tests failed)",
            ),
            recap((first, second)),
        )

    def test_the_full_gates_follow_the_quick_ones(self) -> None:
        entry = self._iteration(
            1,
            gates=(self._gate("lint", passed=True),),
            full_gates=(
                self._gate("coverage", passed=True),
                self._gate("mutation", passed=False, summary="3 mutants survived"),
            ),
        )

        self.assertEqual(
            (
                "Iteration 1: scope OK; quick gates: 1 of 1 passed;"
                " full gates: 1 of 2 passed; failed: mutation (3 mutants survived)",
            ),
            recap((entry,)),
        )

    def test_the_review_says_what_it_decided_against(self) -> None:
        result = {
            "criteria": [
                {"id": "AC1", "status": "satisfied", "evidence": "x"},
                {"id": "AC2", "status": "not_satisfied", "evidence": "y"},
            ],
            "findings": [
                {"severity": "major", "message": "m"},
                {"severity": "minor", "message": "n"},
            ],
        }
        entry = self._iteration(
            1,
            gates=(self._gate("lint", passed=True),),
            full_gates=(self._gate("coverage", passed=True),),
            review=review_block(result),
        )

        self.assertEqual(
            (
                "Iteration 1: scope OK; quick gates: 1 of 1 passed;"
                " full gates: 1 of 1 passed;"
                " review: 1 of 2 criteria not satisfied (AC2), 1 blocking finding",
            ),
            recap((entry,)),
        )

    def test_a_review_without_an_answer_says_so(self) -> None:
        entry = self._iteration(
            1,
            gates=(self._gate("lint", passed=True),),
            full_gates=(self._gate("coverage", passed=True),),
            review=review_block(None),
        )

        self.assertTrue(recap((entry,))[0].endswith("; review: no answer"))

    def test_an_iteration_the_loop_never_measured_is_said_to_be_unmeasured(
        self,
    ) -> None:
        unmeasured = Iteration(iteration=1, feedback_received="", input_state=SNAPSHOT)

        self.assertEqual(("Iteration 1: not measured",), recap((unmeasured,)))


if __name__ == "__main__":
    unittest.main()
