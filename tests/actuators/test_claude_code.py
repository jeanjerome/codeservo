import json
import tempfile
import unittest
from pathlib import Path

from codeservo.actuators.claude_code import (
    IMPLEMENTER_TOOLS,
    REVIEWER_TOOLS,
    ClaudeCodeError,
    _base_command,
    _implementer_command,
    _inline_schema,
    _models,
    _observed,
    _review_result,
    _reviewer_command,
    describe_isolation,
)
from codeservo.runtime.sandbox import Isolation

REVIEW_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["criteria", "findings"],
}


class CommandTests(unittest.TestCase):
    def test_implementer_runs_without_user_configuration(self) -> None:
        command = _base_command(model="opus", tools=IMPLEMENTER_TOOLS)

        self.assertEqual("claude", command[0])
        for flag in (
            "--print",
            "--safe-mode",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
        ):
            self.assertIn(flag, command)
        self.assertEqual(
            "bypassPermissions", command[command.index("--permission-mode") + 1]
        )
        self.assertEqual("opus", command[command.index("--model") + 1])
        self.assertEqual(IMPLEMENTER_TOOLS, command[command.index("--tools") + 1])

    def test_reviewer_cannot_reach_editing_tools(self) -> None:
        command = _base_command(model=None, tools=REVIEWER_TOOLS)
        tools = command[command.index("--tools") + 1].split(",")

        self.assertNotIn("Write", tools)
        self.assertNotIn("Edit", tools)
        self.assertIn("Read", tools)

    def test_omits_the_model_flag_when_unset(self) -> None:
        self.assertNotIn("--model", _base_command(model=None, tools=REVIEWER_TOOLS))

    def test_passes_a_requested_effort_under_the_flag_the_cli_accepts(self) -> None:
        command = _base_command(model=None, tools=IMPLEMENTER_TOOLS, effort="xhigh")

        self.assertEqual("xhigh", command[command.index("--effort") + 1])

    def test_leaves_the_backend_default_when_no_effort_is_requested(self) -> None:
        self.assertNotIn(
            "--effort", _base_command(model=None, tools=IMPLEMENTER_TOOLS)
        )

    def test_the_base_command_carries_no_profile_of_its_own(self) -> None:
        command = _base_command(model="opus", tools=REVIEWER_TOOLS)

        self.assertNotIn("--effort", command)
        self.assertNotIn("--settings", command)


class ImplementerProfileTests(unittest.TestCase):
    def _built(self, **overrides) -> tuple[list[str], dict, Path]:
        request = {"model": "opus", "effort": None, "speed": "standard"}
        request.update(overrides)
        directory = Path(tempfile.mkdtemp())
        command, native = _implementer_command(settings_dir=directory, **request)
        return command, native, directory

    def test_the_standard_speed_adds_no_settings_document(self) -> None:
        command, native, directory = self._built(effort="high")

        self.assertNotIn("--settings", command)
        self.assertEqual({"--effort": "high"}, native)
        self.assertEqual([], sorted(directory.iterdir()))

    def test_the_fast_speed_points_at_a_document_codeservo_writes(self) -> None:
        command, native, directory = self._built(effort="high", speed="fast")
        settings_path = Path(command[command.index("--settings") + 1])

        self.assertTrue(settings_path.is_relative_to(directory))
        self.assertEqual(
            {"fastMode": True},
            json.loads(settings_path.read_text(encoding="utf-8")),
        )
        # The document outlives no run, so the record keeps its content.
        self.assertEqual(
            {"--effort": "high", "--settings": {"fastMode": True}}, native
        )
        self.assertNotIn(str(settings_path), json.dumps(native))

    def test_keeps_the_hermetic_flags_and_adds_no_other_settings_source(
        self,
    ) -> None:
        command, _, _ = self._built(effort="max", speed="fast")

        for flag in (
            "--print",
            "--safe-mode",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
        ):
            self.assertIn(flag, command)
        self.assertEqual(1, command.count("--settings"))

    def test_records_nothing_when_no_profile_was_requested(self) -> None:
        command, native, _ = self._built()

        self.assertEqual({}, native)
        self.assertNotIn("--effort", command)
        self.assertNotIn("--settings", command)


class ReviewerProfileTests(unittest.TestCase):
    """The reviewer carries its own profile on the read-only command."""

    def _built(self, **overrides) -> tuple[list[str], dict, Path]:
        request = {"model": "opus", "effort": None, "speed": "standard"}
        request.update(overrides)
        directory = Path(tempfile.mkdtemp())
        schema_path = directory / "review.schema.json"
        schema_path.write_text(json.dumps(REVIEW_SCHEMA), encoding="utf-8")
        command, native = _reviewer_command(
            settings_dir=directory, schema_path=schema_path, **request
        )
        return command, native, directory

    def test_keeps_the_read_only_review_command_the_backend_answers(self) -> None:
        command, _, _ = self._built(effort="high", speed="fast")

        self.assertIn("--safe-mode", command)
        self.assertEqual(REVIEWER_TOOLS, command[command.index("--tools") + 1])
        self.assertEqual("json", command[command.index("--output-format") + 1])
        schema = json.loads(command[command.index("--json-schema") + 1])
        self.assertNotIn("$schema", schema)
        self.assertEqual(["criteria", "findings"], schema["required"])

    def test_passes_a_requested_effort_under_the_flag_the_cli_accepts(self) -> None:
        command, native, _ = self._built(effort="xhigh")

        self.assertEqual("xhigh", command[command.index("--effort") + 1])
        self.assertEqual({"--effort": "xhigh"}, native)

    def test_the_fast_speed_points_at_a_document_codeservo_writes(self) -> None:
        command, native, directory = self._built(effort="high", speed="fast")
        settings_path = Path(command[command.index("--settings") + 1])

        self.assertTrue(settings_path.is_relative_to(directory))
        self.assertEqual(
            {"fastMode": True},
            json.loads(settings_path.read_text(encoding="utf-8")),
        )
        # The document outlives no run, so the record keeps its content.
        self.assertEqual(
            {"--effort": "high", "--settings": {"fastMode": True}}, native
        )
        self.assertNotIn(str(settings_path), json.dumps(native))
        # No settings source outside CodeServo is added.
        self.assertEqual(1, command.count("--settings"))

    def test_the_standard_speed_adds_no_settings_document(self) -> None:
        command, native, directory = self._built(effort="high")

        self.assertNotIn("--settings", command)
        self.assertEqual({"--effort": "high"}, native)
        self.assertEqual(
            ["review.schema.json"], sorted(item.name for item in directory.iterdir())
        )

    def test_records_nothing_when_no_profile_was_requested(self) -> None:
        command, native, _ = self._built()

        self.assertEqual({}, native)
        self.assertNotIn("--effort", command)
        self.assertNotIn("--settings", command)


class ObservedProfileTests(unittest.TestCase):
    """What Claude Code says about the session, in each of the two roles."""

    def _result(self, **fields: object) -> dict:
        return {"type": "result", "subtype": "success", "result": "done", **fields}

    def test_reports_the_model_and_the_speed_of_an_implementer_stream(self) -> None:
        events = [
            {"type": "system", "subtype": "init", "model": "claude-opus-5"},
            self._result(
                usage={"speed": "standard", "service_tier": "standard"},
                modelUsage={"claude-opus-5": {"outputTokens": 24898}},
            ),
        ]

        self.assertEqual(
            {"model": "claude-opus-5", "effort": None, "speed": "standard"},
            _observed(events).to_document(),
        )

    def test_reads_the_reviewer_model_from_the_one_model_it_billed(self) -> None:
        """The reviewer answers in one object, with no init event to name it."""
        events = [
            self._result(
                usage={"speed": "fast"},
                modelUsage={"claude-opus-5": {"outputTokens": 320}},
            )
        ]

        self.assertEqual(
            {"model": "claude-opus-5", "effort": None, "speed": "fast"},
            _observed(events).to_document(),
        )

    def test_leaves_the_reviewer_model_unread_when_several_were_billed(self) -> None:
        """Choosing among the models a session billed would be an inference."""
        events = [
            self._result(
                usage={"speed": "standard"},
                modelUsage={
                    "claude-opus-5": {"outputTokens": 320},
                    "claude-haiku-4-5-20251001": {"outputTokens": 15},
                },
            )
        ]

        self.assertEqual(
            {"model": None, "effort": None, "speed": "standard"},
            _observed(events).to_document(),
        )

    def test_prefers_the_model_the_stream_resolved_over_what_it_billed(self) -> None:
        events = [
            {"type": "system", "subtype": "init", "model": "claude-opus-5"},
            self._result(
                modelUsage={
                    "claude-opus-5": {"outputTokens": 320},
                    "claude-haiku-4-5-20251001": {"outputTokens": 15},
                }
            ),
        ]

        self.assertEqual("claude-opus-5", _observed(events).model)

    def test_reports_the_model_the_session_named_not_the_one_requested(self) -> None:
        events = [{"type": "system", "subtype": "init", "model": "claude-sonnet-5"}]

        self.assertEqual("claude-sonnet-5", _observed(events).model)

    def test_reports_no_effort_beside_a_speed_the_session_does_name(self) -> None:
        """`fast_mode_state` is a speed; nothing in the stream is an effort."""
        events = [
            self._result(
                usage={"speed": "fast"},
                fast_mode_state="on",
                fast_mode_disabled_reason=None,
            )
        ]

        observed = _observed(events)

        self.assertIsNone(observed.effort)
        self.assertEqual("fast", observed.speed)

    def test_leaves_unreported_values_unknown(self) -> None:
        self.assertEqual(
            {"model": None, "effort": None, "speed": None},
            _observed([{"type": "result", "result": "done"}]).to_document(),
        )

    def test_leaves_every_value_unknown_when_nothing_was_read(self) -> None:
        self.assertEqual(
            {"model": None, "effort": None, "speed": None}, _observed([]).to_document()
        )


class SchemaTests(unittest.TestCase):
    def test_drops_the_unsupported_dialect_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            schema_path = Path(temp) / "review.schema.json"
            schema_path.write_text(
                json.dumps(
                    {
                        "$schema": "https://json-schema.org/draft/2020-12/schema",
                        "type": "object",
                        "required": ["criteria"],
                    }
                ),
                encoding="utf-8",
            )

            schema = json.loads(_inline_schema(schema_path))

            self.assertNotIn("$schema", schema)
            self.assertEqual("object", schema["type"])

    def test_rejects_an_unreadable_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            schema_path = Path(temp) / "review.schema.json"
            schema_path.write_text("{", encoding="utf-8")

            with self.assertRaisesRegex(ClaudeCodeError, "invalid review schema"):
                _inline_schema(schema_path)


class ReviewResultTests(unittest.TestCase):
    def _stdout(self, payload: object) -> tuple[Path, Path]:
        temp = Path(tempfile.mkdtemp())
        stdout_path = temp / "stdout.log"
        stdout_path.write_text(json.dumps(payload), encoding="utf-8")
        return stdout_path, temp / "stderr.log"

    def test_prefers_the_structured_output(self) -> None:
        review = {"criteria": [{"id": "AC1", "status": "satisfied"}], "findings": []}
        stdout_path, stderr_path = self._stdout(
            {"is_error": False, "result": "ignored", "structured_output": review}
        )

        self.assertEqual(review, _review_result(stdout_path, stderr_path))

    def test_falls_back_to_the_serialized_result(self) -> None:
        review = {"criteria": [], "findings": []}
        stdout_path, stderr_path = self._stdout(
            {"is_error": False, "result": json.dumps(review)}
        )

        self.assertEqual(review, _review_result(stdout_path, stderr_path))

    def test_rejects_a_failed_review_session(self) -> None:
        stdout_path, stderr_path = self._stdout(
            {"is_error": True, "result": "Not logged in"}
        )

        with self.assertRaisesRegex(ClaudeCodeError, "reviewer reported an error"):
            _review_result(stdout_path, stderr_path)

    def test_rejects_free_text_instead_of_schema_output(self) -> None:
        stdout_path, stderr_path = self._stdout(
            {"is_error": False, "result": "the change looks fine"}
        )

        with self.assertRaisesRegex(ClaudeCodeError, "schema-shaped output"):
            _review_result(stdout_path, stderr_path)


class ModelRecordTests(unittest.TestCase):
    def test_reports_the_resolved_model_and_everything_that_spent_tokens(self) -> None:
        events = [
            {"type": "system", "subtype": "init", "model": "claude-opus-5"},
            {
                "type": "result",
                "modelUsage": {
                    "claude-opus-5": {"outputTokens": 24898, "costUSD": 1.56},
                    "claude-haiku-4-5-20251001": {
                        "outputTokens": 15,
                        "costUSD": 0.0024,
                    },
                },
            },
        ]

        models = _models(events)

        self.assertEqual("claude-opus-5", models.session_model)
        self.assertEqual(
            {"output_tokens": 24898, "cost_usd": 1.56},
            models.usage["claude-opus-5"],
        )
        self.assertEqual(15, models.usage["claude-haiku-4-5-20251001"]["output_tokens"])

    def test_reports_no_model_when_the_session_names_none(self) -> None:
        models = _models([{"type": "result", "result": "done"}])

        self.assertIsNone(models.session_model)
        self.assertEqual({}, models.usage)


class IsolationTests(unittest.TestCase):
    def test_always_reports_the_controller_owned_mechanism(self) -> None:
        self.assertEqual(
            "macos-sandbox-exec", describe_isolation(Isolation()).mechanism
        )


if __name__ == "__main__":
    unittest.main()
