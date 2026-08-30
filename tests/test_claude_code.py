import json
import tempfile
import unittest
from pathlib import Path

from codeservo.claude_code import (
    ClaudeCodeError,
    IMPLEMENTER_TOOLS,
    REVIEWER_TOOLS,
    _base_command,
    _inline_schema,
    _models,
    _review_result,
    describe_isolation,
)
from codeservo.sandbox import Isolation


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

        self.assertEqual("claude-opus-5", models["session_model"])
        self.assertEqual(
            {"output_tokens": 24898, "cost_usd": 1.56},
            models["usage"]["claude-opus-5"],
        )
        self.assertEqual(15, models["usage"]["claude-haiku-4-5-20251001"]["output_tokens"])

    def test_reports_no_model_when_the_session_names_none(self) -> None:
        models = _models([{"type": "result", "result": "done"}])

        self.assertIsNone(models["session_model"])
        self.assertEqual({}, models["usage"])


class IsolationTests(unittest.TestCase):
    def test_always_reports_the_controller_owned_mechanism(self) -> None:
        self.assertEqual(
            "macos-sandbox-exec", describe_isolation(Isolation())["mechanism"]
        )


if __name__ == "__main__":
    unittest.main()
