import json
import unittest
from pathlib import Path

from codeservo.model import Constitution, Gate, ReviewPolicy, ScopePolicy
from codeservo.prompts import implementer_prompt, reviewer_prompt
from codeservo.task import Task

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


def _task() -> Task:
    return Task(
        path=Path("TASK.md"),
        raw_text="# Task\n\n- [AC1] observable behavior.\n",
        criteria={"AC1": "observable behavior."},
    )


class ImplementerPromptTests(unittest.TestCase):
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
