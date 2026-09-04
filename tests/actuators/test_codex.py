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
    _usage,
    describe_isolation,
)
from codeservo.runtime.confinement import mechanism
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
        command = _base_command(Path("/tmp/worktree"), "workspace-write", "gpt-5.6-sol", "high")

        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ephemeral", command)
        self.assertEqual("/tmp/worktree", command[command.index("--cd") + 1])


class ProfileCommandTests(unittest.TestCase):
    """Codex takes the model as a flag and the effort as one configuration key."""

    def _command(self, effort="high") -> list[str]:
        return _base_command(Path("/tmp/worktree"), "workspace-write", "gpt-5.6-sol", effort)

    def test_passes_the_effort_as_the_one_override(self) -> None:
        command = self._command(effort="high")
        overrides = [
            command[index + 1]
            for index, item in enumerate(command)
            if item == "-c"
        ]

        self.assertEqual(["model_reasoning_effort=high"], overrides)
        self.assertIn("--ignore-user-config", command)

    def test_passes_the_model_under_its_flag_unchanged(self) -> None:
        command = self._command()

        self.assertEqual("gpt-5.6-sol", command[command.index("--model") + 1])

    def test_introduces_no_key_the_backend_does_not_accept(self) -> None:
        rendered = " ".join(self._command(effort="xhigh"))

        self.assertNotIn("service_tier", rendered)
        self.assertNotIn("model_service_tier", rendered)

class ReviewerProfileCommandTests(unittest.TestCase):
    """The reviewer profile rides on the same `codex exec` the schema does."""

    def _command(self, effort="high") -> list[str]:
        command = _base_command(Path("/tmp/worktree"), "read-only", "gpt-5.6-sol", effort)
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

    def test_introduces_no_key_the_backend_does_not_accept(self) -> None:
        rendered = " ".join(self._command(effort="high"))

        self.assertNotIn("model_service_tier", rendered)


class NativeProfileTests(unittest.TestCase):
    def test_records_the_flag_and_the_key_the_command_carried(self) -> None:
        self.assertEqual(
            {"--model": "gpt-5.6-sol", "model_reasoning_effort": "high"},
            _native_profile("gpt-5.6-sol", "high"),
        )

    def test_records_only_what_the_command_carries(self) -> None:
        """Every recorded value appears in the command that was built."""
        command = _base_command(Path("/tmp/worktree"), "read-only", "gpt-5.6-sol", "high")

        native = _native_profile("gpt-5.6-sol", "high")
        self.assertEqual(native["--model"], command[command.index("--model") + 1])
        self.assertIn(f"model_reasoning_effort={native['model_reasoning_effort']}", command)

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

        # A service tier is not part of the profile a run requests.
        self.assertEqual(
            {"model": "gpt-5.6-sol", "effort": "high"},
            _observed(_events(path)).to_document(),
        )

    def test_leaves_a_field_the_stream_never_carries_unknown(self) -> None:
        path = self._stream({"msg": {"type": "agent_message", "message": "done"}})

        self.assertEqual(
            {"model": None, "effort": None},
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
            {"model": None, "effort": None},
            _observed(_events(path)).to_document(),
        )


class UsageTests(unittest.TestCase):
    """What the stream of codex-cli 0.151.0 reports the session consumed."""

    def _stream(self, *events) -> list[dict]:
        path = Path(tempfile.mkdtemp()) / "events.jsonl"
        path.write_text("".join(f"{json.dumps(event)}\n" for event in events), encoding="utf-8")
        return _events(path)

    def test_reads_a_turn_and_takes_the_uncached_input_out_of_the_total(self) -> None:
        events = self._stream(
            {"type": "thread.started", "thread_id": "0199"},
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 14235,
                    "cached_input_tokens": 10752,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 5,
                    "reasoning_output_tokens": 0,
                },
            },
        )

        usage = _usage(events)

        self.assertIsNone(usage.cache_write_duration)
        (billed,) = usage.billed
        # Codex names no model: the controller rates the block at the requested one.
        self.assertIsNone(billed.model)
        self.assertIsNone(billed.reported_cost_usd)
        self.assertEqual(
            {"input": 3483, "cached_input": 10752, "cache_write": 0, "output": 5, "reasoning": 0},
            billed.tokens.to_document(),
        )

    def test_sums_every_completed_turn(self) -> None:
        turn = {"input_tokens": 100, "cached_input_tokens": 40, "cache_write_input_tokens": 10, "output_tokens": 7, "reasoning_output_tokens": 2}
        events = self._stream(
            {"type": "turn.completed", "usage": turn},
            {"type": "item.completed", "item": {"type": "agent_message"}},
            {"type": "turn.completed", "usage": turn},
        )

        self.assertEqual(
            {"input": 100, "cached_input": 80, "cache_write": 20, "output": 14, "reasoning": 4},
            _usage(events).billed[0].tokens.to_document(),
        )

    def test_a_session_that_completed_no_turn_reported_nothing(self) -> None:
        events = self._stream({"type": "thread.started"}, {"type": "turn.started"})

        self.assertEqual((), _usage(events).billed)

    def test_a_count_of_another_shape_leaves_its_category_unknown(self) -> None:
        events = self._stream(
            {"type": "turn.completed", "usage": {"input_tokens": "many", "output_tokens": 3}}
        )

        tokens = _usage(events).billed[0].tokens
        self.assertIsNone(tokens.input)
        self.assertIsNone(tokens.cached_input)
        self.assertEqual(3, tokens.output)


class IsolationTests(unittest.TestCase):
    def test_reports_the_effective_mechanism(self) -> None:
        self.assertEqual(
            "codex-workspace-write", describe_isolation(Isolation()).mechanism
        )
        self.assertEqual(
            mechanism(),
            describe_isolation(Isolation(denied=(Path("sensors"),))).mechanism,
        )


if __name__ == "__main__":
    unittest.main()
