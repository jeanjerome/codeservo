import unittest
from pathlib import Path

from codeservo.actuator import Isolation
from codeservo.codex import _base_command, _sandbox, describe_isolation


class SandboxSelectionTests(unittest.TestCase):
    def test_keeps_its_own_sandbox_when_the_controller_does_not_confine_it(
        self,
    ) -> None:
        self.assertEqual("workspace-write", _sandbox(Isolation(), "workspace-write"))
        self.assertEqual("read-only", _sandbox(Isolation(), "read-only"))

    def test_delegates_confinement_to_the_controller_profile(self) -> None:
        confined = Isolation(denied=(Path("sensors"),))

        self.assertEqual("danger-full-access", _sandbox(confined, "workspace-write"))
        self.assertEqual("danger-full-access", _sandbox(confined, "read-only"))

    def test_ignores_user_configuration(self) -> None:
        command = _base_command(Path("/tmp/worktree"), "workspace-write", None)

        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ephemeral", command)
        self.assertEqual("/tmp/worktree", command[command.index("--cd") + 1])


class IsolationTests(unittest.TestCase):
    def test_reports_the_effective_mechanism(self) -> None:
        self.assertEqual(
            "codex-workspace-write", describe_isolation(Isolation())["mechanism"]
        )
        self.assertEqual(
            "macos-sandbox-exec",
            describe_isolation(Isolation(denied=(Path("sensors"),)))["mechanism"],
        )


if __name__ == "__main__":
    unittest.main()
