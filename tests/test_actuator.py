import os
import unittest
from unittest.mock import patch

from codeservo.actuator import (
    ACTUATOR_ENV_VAR,
    ActuatorError,
    default_actuator_name,
    load_actuator,
)


class ActuatorSelectionTests(unittest.TestCase):
    def test_loads_every_declared_actuator(self) -> None:
        for name in ("claude", "codex"):
            with self.subTest(actuator=name):
                self.assertEqual(name, load_actuator(name).name)

    def test_rejects_unknown_actuator(self) -> None:
        with self.assertRaisesRegex(ActuatorError, "unknown actuator: gpt"):
            load_actuator("gpt")

    def test_environment_selects_the_default_actuator(self) -> None:
        with patch.dict(os.environ, {ACTUATOR_ENV_VAR: "codex"}, clear=False):
            self.assertEqual("codex", default_actuator_name())

    def test_default_actuator_is_claude(self) -> None:
        with patch.dict(os.environ, {ACTUATOR_ENV_VAR: ""}, clear=False):
            self.assertEqual("claude", default_actuator_name())

    def test_rejects_unknown_actuator_in_the_environment(self) -> None:
        with patch.dict(os.environ, {ACTUATOR_ENV_VAR: "gpt"}, clear=False):
            with self.assertRaisesRegex(ActuatorError, "is not one of"):
                default_actuator_name()


if __name__ == "__main__":
    unittest.main()
