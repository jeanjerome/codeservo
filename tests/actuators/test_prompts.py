import json
import tomllib
import unittest
from pathlib import Path

from codeservo.actuators.prompts import implementer_prompt, reviewer_prompt
from codeservo.domain.constitution import (
    Constitution,
    ExecutionEnvironment,
    Gate,
    ReviewPolicy,
    ScopePolicy,
)
from codeservo.domain.task import Task

OBSERVATIONS = {
    "schema_version": 1,
    "gates": [
        {
            "phase": "quick",
            "name": "acceptance",
            "kind": "external_sensor",
            "sensor": "secret/path",
            "passed": True,
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 12,
            "stdout_sha256": "a" * 64,
            "stderr_sha256": "b" * 64,
            "result_sha256": "c" * 64,
            "stdout_tail": "2 passed",
            "stderr_tail": "",
        }
    ],
}
OBSERVATIONS_JSON = json.dumps(
    OBSERVATIONS, sort_keys=True, separators=(",", ":"), ensure_ascii=False
)


def _constitution() -> Constitution:
    return Constitution(
        path=Path("constitution.toml"),
        raw_text='command = "make secret-sensor"\nsensor = "secret/path"\n',
        scope=ScopePolicy(),
        gates=(
            Gate(
                name="acceptance",
                phase="quick",
                command="make secret-sensor",
                baseline=False,
                sensor="secret/path",
            ),
            Gate(name="full", phase="full", command="make check"),
        ),
        review=ReviewPolicy(),
    )


VIEW_HEADER = (
    "ACTUATOR VIEW OF FROZEN REPOSITORY CONSTITUTION\n"
    "================================================\n"
)
FEEDBACK_HEADER = (
    "\nCONTROLLER FEEDBACK FROM THE PREVIOUS ITERATION\n"
    "==============================================="
)


def _mixed_constitution() -> Constitution:
    """One of each kind of gate, plus a sensor gate naming a task."""
    return Constitution(
        path=Path("constitution.toml"),
        raw_text="",
        scope=ScopePolicy(
            protected=(".codeservo/**", "tools/**"),
            max_changed_files=14,
            max_diff_lines=2200,
        ),
        gates=(
            Gate(name="unit", phase="quick", command="make test", timeout_seconds=120),
            Gate(name="coverage", phase="full", task="coverage", timeout_seconds=600),
            Gate(
                name="acceptance",
                phase="quick",
                command="make secret-sensor",
                baseline=False,
                sensor="secret/path",
            ),
            Gate(
                name="contract",
                phase="full",
                task="secret-task",
                baseline=False,
                sensor="secret/other",
            ),
        ),
        review=ReviewPolicy(),
        execution=ExecutionEnvironment(
            provider="pixi", manifest="pyproject.toml", lock="pixi.lock"
        ),
    )


def _view(constitution: Constitution) -> dict:
    """The projection the implementer prompt carries, read back as TOML."""
    prompt = implementer_prompt(_task(), constitution, "")
    _, header, after = prompt.partition(VIEW_HEADER)
    if not header:
        raise AssertionError("the prompt lacks the view header")
    view, footer, _ = after.partition(FEEDBACK_HEADER)
    if not footer:
        raise AssertionError("the prompt lacks the feedback header")
    return tomllib.loads(view)


def _task() -> Task:
    return Task(
        path=Path("TASK.md"),
        raw_text="# Task\n\n- [AC1] observable behavior.\n",
        criteria={"AC1": "observable behavior."},
    )


class ImplementerPromptTests(unittest.TestCase):
    def test_lists_every_acceptance_criterion_by_its_id(self) -> None:
        task = Task(
            path=Path("TASK.md"),
            raw_text="# Task\n\n- [AC1] one.\n- [AC2] two.\n",
            criteria={"AC1": "one.", "AC2": "two."},
        )

        prompt = implementer_prompt(task, _constitution(), "")

        self.assertIn(
            "ACCEPTANCE CRITERIA\n===================\n- AC1: one.\n- AC2: two.\n",
            prompt,
        )
        self.assertLess(prompt.index("ACCEPTANCE CRITERIA"), prompt.index("TASK\n===="))

    def test_the_first_iteration_is_told_nothing(self) -> None:
        prompt = implementer_prompt(_task(), _constitution(), "")

        self.assertIn(
            FEEDBACK_HEADER + "\nNone. This is the first iteration.\n", prompt
        )
        self.assertNotIn("Iterations so far", prompt)

    def test_a_later_iteration_is_told_every_iteration_then_the_last_in_full(
        self,
    ) -> None:
        history = (
            "Iteration 1: scope OK; quick gates: 1 of 2 passed; failed: unit (3 failed)",
            "Iteration 2: scope OK; quick gates: 1 of 2 passed; failed: unit (1 failed)",
        )

        prompt = implementer_prompt(
            _task(), _constitution(), "Gate unit FAILED\n...", history
        )

        self.assertIn(
            FEEDBACK_HEADER
            + "\nIterations so far:\n"
            + f"- {history[0]}\n"
            + f"- {history[1]}\n"
            + "\nFeedback from the previous iteration:\n"
            + "Gate unit FAILED\n...\n",
            prompt,
        )
        self.assertNotIn("None. This is the first iteration.", prompt)

    def test_redacts_external_sensor_command_and_reference(self) -> None:
        prompt = implementer_prompt(_task(), _constitution(), "")

        self.assertNotIn("make secret-sensor", prompt)
        self.assertNotIn("secret/path", prompt)
        self.assertIn("<controller-owned sensor>", prompt)
        self.assertIn("make check", prompt)

    def test_carries_no_gate_observation(self) -> None:
        prompt = implementer_prompt(_task(), _constitution(), "")

        self.assertNotIn("CONTROLLER OBSERVATIONS", prompt)
        self.assertNotIn("2 passed", prompt)
        self.assertNotIn("stdout_tail", prompt)

    def test_the_view_is_a_toml_document_naming_every_gate_in_order(self) -> None:
        view = _view(_mixed_constitution())

        self.assertEqual(
            ["unit", "coverage", "acceptance", "contract"],
            [gate["name"] for gate in view["gate"]],
        )
        self.assertEqual(
            {
                "protected": [".codeservo/**", "tools/**"],
                "max_changed_files": 14,
                "max_diff_lines": 2200,
            },
            view["scope"],
        )
        self.assertEqual({"blocking_severities": ["blocker", "major"]}, view["review"])

    def test_renders_a_command_gate_as_the_command_it_names(self) -> None:
        gate = _view(_mixed_constitution())["gate"][0]

        self.assertEqual(
            {
                "name": "unit",
                "phase": "quick",
                "command": "make test",
                "timeout_seconds": 120,
                "baseline": True,
            },
            gate,
        )

    def test_renders_a_task_gate_as_the_task_it_names(self) -> None:
        gate = _view(_mixed_constitution())["gate"][1]

        self.assertEqual(
            {
                "name": "coverage",
                "phase": "full",
                "task": "coverage",
                "timeout_seconds": 600,
                "baseline": True,
            },
            gate,
        )
        self.assertNotIn("command", gate)

    def test_renders_a_sensor_gate_as_the_placeholder_alone(self) -> None:
        gates = _view(_mixed_constitution())["gate"]

        self.assertEqual(
            {
                "name": "acceptance",
                "phase": "quick",
                "command": "<controller-owned sensor>",
                "timeout_seconds": 300,
                "baseline": False,
            },
            gates[2],
        )
        self.assertEqual(
            {
                "name": "contract",
                "phase": "full",
                "command": "<controller-owned sensor>",
                "timeout_seconds": 300,
                "baseline": False,
            },
            gates[3],
        )
        prompt = implementer_prompt(_task(), _mixed_constitution(), "")
        for secret in (
            "make secret-sensor",
            "secret/path",
            "secret-task",
            "secret/other",
        ):
            self.assertNotIn(secret, prompt)

    def test_every_gate_names_exactly_one_measurement(self) -> None:
        for gate in _view(_mixed_constitution())["gate"]:
            with self.subTest(gate=gate["name"]):
                self.assertEqual(
                    1,
                    len({"command", "task"} & set(gate)),
                    f"{gate} names no single measurement",
                )


class ReviewerPromptTests(unittest.TestCase):
    def test_embeds_the_exact_bundle_between_its_markers(self) -> None:
        prompt = reviewer_prompt(_task(), _constitution(), OBSERVATIONS_JSON)

        _, _, after = prompt.partition("BEGIN CONTROLLER OBSERVATIONS JSON\n")
        embedded, marker, _ = after.partition("\nEND CONTROLLER OBSERVATIONS JSON")

        self.assertTrue(marker, "reviewer prompt lacks the closing marker")
        self.assertEqual(OBSERVATIONS_JSON, embedded)
        self.assertEqual(OBSERVATIONS, json.loads(embedded))

    def test_presents_the_bundle_as_controller_owned_evidence(self) -> None:
        prompt = reviewer_prompt(_task(), _constitution(), OBSERVATIONS_JSON)

        self.assertIn(
            "controller-owned deterministic evidence rather than an implementer claim",
            prompt,
        )
        self.assertIn("Do not trust the implementer's claims.", prompt)


if __name__ == "__main__":
    unittest.main()
