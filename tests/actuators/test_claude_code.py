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
    _observed,
    _reported_model,
    _review_result,
    _reviewer_command,
    _session,
    _usage,
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
        command = _base_command(model="claude-opus-5", tools=IMPLEMENTER_TOOLS, effort="high")

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
        self.assertEqual("claude-opus-5", command[command.index("--model") + 1])
        self.assertEqual(IMPLEMENTER_TOOLS, command[command.index("--tools") + 1])

    def test_reviewer_cannot_reach_editing_tools(self) -> None:
        command = _base_command(model="claude-opus-5", tools=REVIEWER_TOOLS, effort="low")
        tools = command[command.index("--tools") + 1].split(",")

        self.assertNotIn("Write", tools)
        self.assertNotIn("Edit", tools)
        self.assertIn("Read", tools)

    def test_passes_the_effort_under_the_flag_the_cli_accepts_unchanged(self) -> None:
        command = _base_command(model="claude-opus-5", tools=IMPLEMENTER_TOOLS, effort="xhigh")

        self.assertEqual("xhigh", command[command.index("--effort") + 1])

    def test_the_command_carries_the_profile_and_no_settings_source(self) -> None:
        command = _base_command(model="claude-opus-5", tools=REVIEWER_TOOLS, effort="high")

        self.assertEqual(1, command.count("--model"))
        self.assertEqual(1, command.count("--effort"))
        self.assertNotIn("--settings", command)

class ImplementerProfileTests(unittest.TestCase):
    def test_records_the_two_flags_the_command_carried(self) -> None:
        command, native = _implementer_command(model="claude-opus-5", effort="high")

        self.assertEqual({"--model": "claude-opus-5", "--effort": "high"}, native)
        for flag, value in native.items():
            self.assertEqual(value, command[command.index(flag) + 1])
        self.assertEqual("stream-json", command[command.index("--output-format") + 1])

    def test_keeps_the_hermetic_flags(self) -> None:
        command, _ = _implementer_command(model="claude-opus-5", effort="high")

        for flag in (
            "--print",
            "--safe-mode",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
        ):
            self.assertIn(flag, command)
        self.assertNotIn("--settings", command)

class ReviewerProfileTests(unittest.TestCase):
    """The reviewer carries its own profile on the read-only command."""

    def _built(self, **overrides) -> tuple[list[str], dict, Path]:
        request = {"model": "claude-opus-5", "effort": "high"}
        request.update(overrides)
        directory = Path(tempfile.mkdtemp())
        schema_path = directory / "review.schema.json"
        schema_path.write_text(json.dumps(REVIEW_SCHEMA), encoding="utf-8")
        command, native = _reviewer_command(schema_path=schema_path, **request)
        return command, native, directory

    def test_keeps_the_read_only_review_command_the_backend_answers(self) -> None:
        command, _, _ = self._built()

        self.assertIn("--safe-mode", command)
        self.assertEqual(REVIEWER_TOOLS, command[command.index("--tools") + 1])
        self.assertEqual("json", command[command.index("--output-format") + 1])
        schema = json.loads(command[command.index("--json-schema") + 1])
        self.assertNotIn("$schema", schema)
        self.assertEqual(["criteria", "findings"], schema["required"])

    def test_passes_the_profile_under_the_flags_the_cli_accepts(self) -> None:
        command, native, _ = self._built(effort="xhigh")

        self.assertEqual("xhigh", command[command.index("--effort") + 1])
        self.assertEqual("claude-opus-5", command[command.index("--model") + 1])
        self.assertEqual({"--model": "claude-opus-5", "--effort": "xhigh"}, native)

    def test_writes_no_settings_document(self) -> None:
        command, _, directory = self._built()

        self.assertNotIn("--settings", command)
        self.assertEqual(
            ["review.schema.json"], sorted(item.name for item in directory.iterdir())
        )

class ObservedProfileTests(unittest.TestCase):
    """What Claude Code says about the session, in each of the two roles."""

    def _result(self, **fields: object) -> dict:
        return {"type": "result", "subtype": "success", "result": "done", **fields}

    def test_reports_the_model_of_an_implementer_stream(self) -> None:
        events = [
            {"type": "system", "subtype": "init", "model": "claude-opus-5"},
            self._result(modelUsage={"claude-opus-5": {"outputTokens": 24898}}),
        ]

        self.assertEqual(
            {"model": "claude-opus-5", "effort": None},
            _observed(events).to_document(),
        )

    def test_reads_the_reviewer_model_from_the_one_model_it_billed(self) -> None:
        events = [self._result(modelUsage={"claude-opus-5": {"outputTokens": 320}})]

        self.assertEqual(
            {"model": "claude-opus-5", "effort": None},
            _observed(events).to_document(),
        )

    def test_leaves_the_reviewer_model_unread_when_several_were_billed(self) -> None:
        events = [
            self._result(
                modelUsage={
                    "claude-opus-5": {"outputTokens": 320},
                    "claude-haiku-4-5-20251001": {"outputTokens": 15},
                },
            )
        ]

        self.assertEqual({"model": None, "effort": None}, _observed(events).to_document())

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

    def test_reports_no_effort_whatever_the_stream_carries(self) -> None:
        """Nothing in either role's stream is a reasoning effort."""
        events = [
            {"type": "system", "subtype": "init", "model": "claude-opus-5"},
            self._result(usage={"speed": "fast"}, fast_mode_state="on"),
        ]

        self.assertIsNone(_observed(events).effort)


class UsageTests(unittest.TestCase):
    """What Claude Code reports the session consumed, under each model it billed."""

    def _result(self, **fields: object) -> dict:
        return {"type": "result", "subtype": "success", "result": "done", **fields}

    def test_reads_the_five_counts_and_the_reported_cost_per_model(self) -> None:
        events = [
            self._result(
                usage={"cache_creation": {"ephemeral_1h_input_tokens": 72277, "ephemeral_5m_input_tokens": 0}},
                modelUsage={
                    "claude-opus-5": {
                        "inputTokens": 92,
                        "outputTokens": 23966,
                        "cacheReadInputTokens": 2297822,
                        "cacheCreationInputTokens": 72277,
                        "thinkingTokens": 10745,
                        "costUSD": 2.471291,
                        "contextWindow": 1000000,
                    },
                    "claude-haiku-4-5-20251001": {"inputTokens": 3232, "outputTokens": 18, "costUSD": 0.003322},
                },
            )
        ]

        usage = _usage(events)

        self.assertEqual("1h", usage.cache_write_duration)
        opus, haiku = usage.billed
        self.assertEqual("claude-opus-5", opus.model)
        self.assertEqual(
            {"input": 92, "cached_input": 2297822, "cache_write": 72277, "output": 23966, "reasoning": 10745},
            opus.tokens.to_document(),
        )
        self.assertEqual(2.471291, opus.reported_cost_usd)
        # A count the block did not carry stays empty rather than zero.
        self.assertEqual(
            {"input": 3232, "cached_input": None, "cache_write": None, "output": 18, "reasoning": None},
            haiku.tokens.to_document(),
        )

    def test_names_the_one_duration_the_session_wrote_with(self) -> None:
        cases = (
            ({"ephemeral_1h_input_tokens": 10, "ephemeral_5m_input_tokens": 0}, "1h"),
            ({"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 10}, "5m"),
            ({"ephemeral_1h_input_tokens": 10, "ephemeral_5m_input_tokens": 10}, "mixed"),
            ({"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 0}, None),
            ({}, None),
        )
        for creation, expected in cases:
            with self.subTest(creation=creation):
                events = [self._result(usage={"cache_creation": creation}, modelUsage={})]

                self.assertEqual(expected, _usage(events).cache_write_duration)

    def test_a_stream_without_a_result_reports_nothing(self) -> None:
        usage = _usage([{"type": "system", "subtype": "init", "model": "claude-opus-5"}])

        self.assertEqual((), usage.billed)
        self.assertIsNone(usage.cache_write_duration)

    def test_a_count_of_another_shape_is_not_a_count(self) -> None:
        events = [self._result(modelUsage={"claude-opus-5": {"inputTokens": "12", "outputTokens": True, "costUSD": "free"}})]

        billed = _usage(events).billed[0]

        self.assertIsNone(billed.tokens.input)
        self.assertIsNone(billed.tokens.output)
        self.assertIsNone(billed.reported_cost_usd)

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

    def test_rejects_an_answer_that_is_not_text(self) -> None:
        """Bytes no decoder accepts are not a review, and say so by name."""
        stdout_path, stderr_path = self._stdout(None)
        stdout_path.write_bytes(b"\x80\x81")

        with self.assertRaisesRegex(ClaudeCodeError, "invalid reviewer output"):
            _review_result(stdout_path, stderr_path)


class ModelRecordTests(unittest.TestCase):
    """The model a session names, and the numbers a record can carry back."""

    def _result(self, **fields: object) -> dict:
        return {"type": "result", "subtype": "success", "result": "done", **fields}

    def test_reports_the_resolved_model_and_everything_that_spent_tokens(self) -> None:
        events = [
            {"type": "system", "subtype": "init", "model": "claude-opus-5"},
            self._result(
                modelUsage={
                    "claude-opus-5": {"outputTokens": 320, "costUSD": 1.5},
                    "claude-haiku-4-5-20251001": {"outputTokens": 15, "costUSD": 0.01},
                }
            ),
        ]

        self.assertEqual("claude-opus-5", _reported_model(events))
        self.assertEqual(
            ["claude-opus-5", "claude-haiku-4-5-20251001"],
            [billed.model for billed in _usage(events).billed],
        )

    def test_reports_no_model_when_the_session_names_none(self) -> None:
        self.assertIsNone(_reported_model([self._result()]))
        self.assertEqual((), _usage([self._result()]).billed)

    def test_reports_no_model_where_the_stream_names_one_of_another_shape(self) -> None:
        events = [
            {"type": "system", "subtype": "init", "model": 12},
            self._result(modelUsage={"claude-opus-5": ["not", "a", "record"]}),
        ]

        self.assertIsNone(_reported_model(events))
        self.assertEqual((), _usage(events).billed)

    def test_bills_only_the_numbers_json_can_carry_back(self) -> None:
        """`json.loads` reads NaN and Infinity; nothing else reads them back."""
        for amount in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(amount=amount):
                events = [self._result(modelUsage={"claude-opus-5": {"costUSD": amount}})]

                self.assertIsNone(_usage(events).billed[0].reported_cost_usd)

class SessionRecordTests(unittest.TestCase):
    """The result event is read as the record declares it, or not at all."""

    def test_reads_every_field_the_result_event_names(self) -> None:
        session = _session(
            {
                "session_id": "abc",
                "subtype": "success",
                "is_error": False,
                "num_turns": 12,
                "total_cost_usd": 1.5,
                "terminal_reason": "end_turn",
            }
        )

        self.assertEqual("abc", session.session_id)
        self.assertEqual("success", session.subtype)
        self.assertFalse(session.is_error)
        self.assertEqual(12, session.num_turns)
        self.assertEqual(1.5, session.total_cost_usd)
        self.assertEqual("end_turn", session.terminal_reason)

    def test_reports_nothing_for_a_field_carrying_another_shape(self) -> None:
        """A mapping where a count belongs is not a measurement to record."""
        session = _session(
            {
                "session_id": ["abc"],
                "num_turns": "twelve",
                "total_cost_usd": float("inf"),
                "terminal_reason": {"why": "end_turn"},
            }
        )

        self.assertIsNone(session.session_id)
        self.assertIsNone(session.num_turns)
        self.assertIsNone(session.total_cost_usd)
        self.assertIsNone(session.terminal_reason)

    def test_reads_a_whole_number_of_dollars_as_a_number(self) -> None:
        self.assertEqual(0.0, _session({"total_cost_usd": 0}).total_cost_usd)


class IsolationTests(unittest.TestCase):
    def test_always_reports_the_controller_owned_mechanism(self) -> None:
        self.assertEqual(
            "macos-sandbox-exec", describe_isolation(Isolation()).mechanism
        )


if __name__ == "__main__":
    unittest.main()
