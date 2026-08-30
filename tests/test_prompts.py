import unittest
from pathlib import Path

from codeservo.model import Constitution, Gate, ReviewPolicy, ScopePolicy
from codeservo.prompts import implementer_prompt
from codeservo.task import Task


class ImplementerPromptTests(unittest.TestCase):
    def test_redacts_external_sensor_command_and_reference(self) -> None:
        constitution = Constitution(
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
        task = Task(
            path=Path("TASK.md"),
            raw_text="# Task\n\n- [AC1] observable behavior.\n",
            criteria={"AC1": "observable behavior."},
        )

        prompt = implementer_prompt(task, constitution, "")

        self.assertNotIn("make secret-sensor", prompt)
        self.assertNotIn("secret/path", prompt)
        self.assertIn("<controller-owned sensor>", prompt)
        self.assertIn("make check", prompt)


if __name__ == "__main__":
    unittest.main()
