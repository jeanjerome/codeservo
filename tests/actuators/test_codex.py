import json
import tempfile
import unittest
from pathlib import Path

from codeservo.actuators.codex import (
    _base_command,
    _events,
    _native_profile,
    _observed,
    _sandbox,
    describe_isolation,
)
from codeservo.runtime.sandbox import Isolation


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


class ProfileCommandTests(unittest.TestCase):
    """Codex accepts `model_reasoning_effort` and `service_tier`, and no other."""

    def _command(self, effort=None, speed="standard") -> list[str]:
        return _base_command(
            Path("/tmp/worktree"), "workspace-write", "gpt-5.6-sol", effort, speed
        )

    def test_passes_a_requested_effort_as_an_override(self) -> None:
        command = self._command(effort="high")
        overrides = [
            command[index + 1]
            for index, item in enumerate(command)
            if item == "-c"
        ]

        self.assertEqual(["model_reasoning_effort=high"], overrides)
        self.assertIn("--ignore-user-config", command)

    def test_passes_the_fast_speed_as_the_priority_service_tier(self) -> None:
        command = self._command(effort="high", speed="fast")

        self.assertEqual(
            ["model_reasoning_effort=high", "service_tier=priority"],
            [
                command[index + 1]
                for index, item in enumerate(command)
                if item == "-c"
            ],
        )

    def test_the_standard_speed_overrides_no_service_tier(self) -> None:
        command = self._command(effort="high", speed="standard")

        self.assertNotIn("service_tier=priority", command)

    def test_leaves_the_backend_defaults_when_nothing_is_requested(self) -> None:
        self.assertNotIn("-c", self._command())

    def test_introduces_no_key_the_backend_does_not_accept(self) -> None:
        rendered = " ".join(self._command(effort="high", speed="fast"))

        self.assertNotIn("model_service_tier", rendered)

    def test_a_command_carries_no_profile_it_was_not_given(self) -> None:
        command = _base_command(Path("/tmp/worktree"), "read-only", "gpt-5.6-sol")

        self.assertNotIn("-c", command)


class ReviewerProfileCommandTests(unittest.TestCase):
    """The reviewer profile rides on the same `codex exec` the schema does."""

    def _command(self, effort=None, speed="standard") -> list[str]:
        command = _base_command(
            Path("/tmp/worktree"), "read-only", "gpt-5.6-sol", effort, speed
        )
        command.extend(
            [
                "--output-schema",
                "/tmp/review.schema.json",
                "--output-last-message",
                "/tmp/review.json",
                "-",
            ]
        )
        return command

    def _overrides(self, command: list[str]) -> list[str]:
        return [
            command[index + 1]
            for index, item in enumerate(command)
            if item == "-c"
        ]

    def test_passes_the_requested_effort_on_the_command_carrying_the_schema(
        self,
    ) -> None:
        command = self._command(effort="high")

        self.assertEqual(["model_reasoning_effort=high"], self._overrides(command))
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--output-schema", command)
        self.assertLess(command.index("-c"), command.index("--output-schema"))

    def test_passes_the_fast_speed_as_the_priority_service_tier(self) -> None:
        command = self._command(effort="low", speed="fast")

        self.assertEqual(
            ["model_reasoning_effort=low", "service_tier=priority"],
            self._overrides(command),
        )

    def test_the_standard_speed_overrides_no_service_tier(self) -> None:
        self.assertNotIn("service_tier=priority", self._command(effort="low"))

    def test_leaves_the_backend_defaults_when_nothing_is_requested(self) -> None:
        command = self._command()

        self.assertEqual([], self._overrides(command))
        self.assertIn("--ignore-user-config", command)

    def test_introduces_no_key_the_backend_does_not_accept(self) -> None:
        rendered = " ".join(self._command(effort="high", speed="fast"))

        self.assertNotIn("model_service_tier", rendered)


class NativeProfileTests(unittest.TestCase):
    def test_records_the_keys_the_command_actually_carried(self) -> None:
        self.assertEqual(
            {"model_reasoning_effort": "high", "service_tier": "priority"},
            _native_profile("high", "fast"),
        )
        self.assertEqual(
            {"model_reasoning_effort": "low"}, _native_profile("low", "standard")
        )
        self.assertEqual({}, _native_profile(None, "standard"))

    def test_records_only_keys_the_command_carries(self) -> None:
        """Every recorded key appears in the command that was built."""
        command = _base_command(
            Path("/tmp/worktree"), "read-only", "gpt-5.6-sol", "high", "fast"
        )

        for key, value in _native_profile("high", "fast").items():
            self.assertIn(f"{key}={value}", command)


class ObservedProfileTests(unittest.TestCase):
    def _stream(self, *events) -> Path:
        path = Path(tempfile.mkdtemp()) / "events.jsonl"
        path.write_text(
            "".join(f"{json.dumps(event)}\n" for event in events),
            encoding="utf-8",
        )
        return path

    def test_reads_the_fields_the_stream_reports_not_the_keys_it_was_sent(
        self,
    ) -> None:
        path = self._stream(
            {
                "msg": {
                    "type": "session_configured",
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "high",
                    "service_tier": "priority",
                }
            }
        )

        self.assertEqual(
            {"model": "gpt-5.6-sol", "effort": "high", "speed": "priority"},
            _observed(_events(path)).to_document(),
        )

    def test_leaves_a_field_the_stream_never_carries_unknown(self) -> None:
        path = self._stream({"msg": {"type": "agent_message", "message": "done"}})

        self.assertEqual(
            {"model": None, "effort": None, "speed": None},
            _observed(_events(path)).to_document(),
        )

    def test_keeps_the_last_report_of_the_session(self) -> None:
        path = self._stream(
            {"model": "first", "reasoning_effort": "low"},
            {"msg": {"type": "agent_message", "message": "working"}},
            {"model": "second"},
        )

        observed = _observed(_events(path))

        self.assertEqual("second", observed.model)
        self.assertEqual("low", observed.effort)

    def test_reports_the_model_the_stream_names_not_the_one_requested(self) -> None:
        path = self._stream({"msg": {"model": "gpt-5.6-codex"}})

        self.assertEqual("gpt-5.6-codex", _observed(_events(path)).model)

    def test_leaves_the_installed_stream_reporting_no_profile_at_all(self) -> None:
        """The event stream of codex-cli 0.151.0, in either role."""
        path = self._stream(
            {"type": "thread.started", "thread_id": "0199"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "agent_message"}},
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 12, "output_tokens": 3},
            },
        )

        self.assertEqual(
            {"model": None, "effort": None, "speed": None},
            _observed(_events(path)).to_document(),
        )


class IsolationTests(unittest.TestCase):
    def test_reports_the_effective_mechanism(self) -> None:
        self.assertEqual(
            "codex-workspace-write", describe_isolation(Isolation()).mechanism
        )
        self.assertEqual(
            "macos-sandbox-exec",
            describe_isolation(Isolation(denied=(Path("sensors"),))).mechanism,
        )


if __name__ == "__main__":
    unittest.main()
