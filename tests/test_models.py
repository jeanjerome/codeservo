"""The model inventory projects local provider caches and reads nothing else."""

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from codeservo.cli import main
from codeservo.models import (
    BACKEND_NAMES,
    CLAUDE_UNVERIFIED_REASON,
    PROFILE_SUPPORTED,
    PROFILE_UNSUPPORTED,
    PROFILE_UNVERIFIED,
    SCHEMA_VERSION,
    ModelSelectionError,
    build_inventory,
    claude_cache_path,
    codex_cache_path,
    read_claude,
    read_codex,
    render_document,
    validate_profile,
)

BACKEND_FIELDS = {
    "backend",
    "source",
    "source_observed_at",
    "cli_version",
    "skipped_entries",
    "models",
}
# A backend the command could not project states why, so an unavailable entry
# carries the documented fields and that reason.
UNAVAILABLE_BACKEND_FIELDS = BACKEND_FIELDS | {"unavailable_reason"}
MODEL_FIELDS = {
    "backend",
    "model",
    "display_name",
    "efforts",
    "default_effort",
    "speeds",
    "source",
    "source_observed_at",
    "cli_version",
    "status",
    "ineligible_reason",
}


def codex_entry(slug, **overrides):
    entry = {
        "slug": slug,
        "display_name": slug.upper(),
        "description": "a sentence the inventory must not copy",
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": [
            {"effort": "low", "description": "fast"},
            {"effort": "medium", "description": "balanced"},
            {"effort": "high", "description": "deep"},
        ],
        "visibility": "list",
        "additional_speed_tiers": ["fast"],
        "context_window": 400000,
        "model_messages": {"instructions_template": "a prompt to leave behind"},
    }
    entry.update(overrides)
    return entry


def codex_cache(*entries, **overrides):
    document = {
        "fetched_at": "2026-08-31T21:49:12.689027Z",
        "etag": 'W/"an etag the inventory must not copy"',
        "client_version": "0.151.0",
        "models": list(entries),
    }
    document.update(overrides)
    return document


class CacheLocationTests(unittest.TestCase):
    def test_codex_prefers_its_home_variable(self) -> None:
        self.assertEqual(
            Path("/elsewhere/models_cache.json"),
            codex_cache_path({"CODEX_HOME": "/elsewhere", "HOME": "/home/user"}),
        )

    def test_codex_falls_back_to_the_user_directory(self) -> None:
        self.assertEqual(
            Path("/home/user/.codex/models_cache.json"),
            codex_cache_path({"HOME": "/home/user"}),
        )

    def test_claude_declares_its_configured_and_default_paths(self) -> None:
        self.assertEqual(
            Path("/home/user/.claude/models_cache.json"),
            claude_cache_path({"HOME": "/home/user"}),
        )
        self.assertEqual(
            Path("/config/models_cache.json"),
            claude_cache_path({"CLAUDE_CONFIG_DIR": "/config", "HOME": "/home/user"}),
        )


class CodexProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.env = {"CODEX_HOME": str(self.home), "HOME": str(self.home)}

    def write_cache(self, document) -> Path:
        path = self.home / "models_cache.json"
        path.write_text(
            document if isinstance(document, str) else json.dumps(document),
            encoding="utf-8",
        )
        return path

    def test_projects_the_declared_model_shape(self) -> None:
        self.write_cache(codex_cache(codex_entry("gpt-5.6-sol")))

        backend = read_codex(self.env)

        self.assertEqual("backend-cache", backend["source"])
        self.assertEqual("2026-08-31T21:49:12.689027Z", backend["source_observed_at"])
        self.assertEqual("0.151.0", backend["cli_version"])
        self.assertEqual(0, backend["skipped_entries"])
        self.assertEqual(
            [
                {
                    "backend": "codex",
                    "model": "gpt-5.6-sol",
                    "display_name": "GPT-5.6-SOL",
                    "efforts": ["low", "medium", "high"],
                    "default_effort": "medium",
                    "speeds": ["standard", "fast"],
                    "source": "backend-cache",
                    "source_observed_at": "2026-08-31T21:49:12.689027Z",
                    "cli_version": "0.151.0",
                    "status": "advertised",
                    "ineligible_reason": None,
                }
            ],
            backend["models"],
        )

    def test_reports_the_standard_speed_without_a_fast_tier(self) -> None:
        self.write_cache(codex_cache(codex_entry("m", additional_speed_tiers=[])))

        self.assertEqual(["standard"], read_codex(self.env)["models"][0]["speeds"])

    def test_records_a_default_effort_only_when_it_is_supported(self) -> None:
        self.write_cache(
            codex_cache(
                codex_entry("supported", default_reasoning_level="high"),
                codex_entry("unsupported", default_reasoning_level="ultra"),
            )
        )

        models = read_codex(self.env)["models"]

        self.assertEqual("high", models[0]["default_effort"])
        self.assertIsNone(models[1]["default_effort"])

    def test_reports_a_hidden_model_as_ineligible(self) -> None:
        self.write_cache(
            codex_cache(
                codex_entry("listed"),
                codex_entry("hidden", visibility="hide"),
            )
        )

        models = read_codex(self.env)["models"]

        self.assertEqual("advertised", models[0]["status"])
        self.assertIsNone(models[0]["ineligible_reason"])
        self.assertEqual("ineligible", models[1]["status"])
        self.assertTrue(models[1]["ineligible_reason"])

    def test_reports_an_absent_cache_as_unavailable(self) -> None:
        backend = read_codex(self.env)

        self.assertEqual("unavailable", backend["source"])
        self.assertIn("no model cache", backend["unavailable_reason"])
        self.assertEqual([], backend["models"])
        self.assertEqual(0, backend["skipped_entries"])
        self.assertIsNone(backend["source_observed_at"])
        self.assertIsNone(backend["cli_version"])

    def test_reports_a_malformed_document_as_unavailable(self) -> None:
        for document in ("{not json", json.dumps([]), json.dumps({"models": "none"})):
            with self.subTest(document=document):
                self.write_cache(document)

                backend = read_codex(self.env)

                self.assertEqual("unavailable", backend["source"])
                self.assertTrue(backend["unavailable_reason"])
                self.assertEqual([], backend["models"])

    def test_reports_an_unreadable_cache_as_unavailable(self) -> None:
        path = self.home / "models_cache.json"
        path.mkdir()

        backend = read_codex(self.env)

        self.assertEqual("unavailable", backend["source"])
        self.assertTrue(backend["unavailable_reason"])

    def test_skips_a_malformed_entry_among_conforming_ones(self) -> None:
        self.write_cache(
            codex_cache(
                codex_entry("first"),
                "not an entry",
                {"display_name": "no slug"},
                codex_entry("wrong-types", supported_reasoning_levels="low"),
                codex_entry("second"),
            )
        )

        backend = read_codex(self.env)

        self.assertEqual(3, backend["skipped_entries"])
        self.assertEqual(["first", "second"], [m["model"] for m in backend["models"]])

    def test_projects_exactly_the_documented_fields(self) -> None:
        self.write_cache(codex_cache(codex_entry("m")))

        backend = read_codex(self.env)

        self.assertEqual(BACKEND_FIELDS, set(backend))
        self.assertEqual(MODEL_FIELDS, set(backend["models"][0]))

    def test_states_a_reason_only_when_the_source_is_unavailable(self) -> None:
        backend = read_codex(self.env)

        self.assertEqual(UNAVAILABLE_BACKEND_FIELDS, set(backend))

    def test_leaves_every_other_provider_value_out_of_the_document(self) -> None:
        self.write_cache(codex_cache(codex_entry("gpt-5.6-sol")))

        rendered = render_document(build_inventory(actuator="codex", env=self.env))

        self.assertIn("gpt-5.6-sol", rendered)
        for provider_value in ("description", "etag", "context_window", "a prompt"):
            self.assertNotIn(provider_value, rendered)


class ClaudeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = Path(self.temporary.name)
        self.env = {
            "CLAUDE_CONFIG_DIR": str(self.config),
            "HOME": str(self.config),
        }

    def test_reports_no_verified_schema_without_a_cache(self) -> None:
        backend = read_claude(self.env)

        self.assertEqual("unavailable", backend["source"])
        self.assertEqual(CLAUDE_UNVERIFIED_REASON, backend["unavailable_reason"])
        self.assertEqual([], backend["models"])

    def test_applies_no_other_backend_reader_to_an_existing_cache(self) -> None:
        (self.config / "models_cache.json").write_text(
            json.dumps(codex_cache(codex_entry("claude-shaped"))), encoding="utf-8"
        )

        backend = read_claude(self.env)

        self.assertEqual("unavailable", backend["source"])
        self.assertEqual(CLAUDE_UNVERIFIED_REASON, backend["unavailable_reason"])
        self.assertEqual([], backend["models"])
        self.assertIsNone(backend["source_observed_at"])
        self.assertEqual(UNAVAILABLE_BACKEND_FIELDS, set(backend))


class SelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        (self.home / "models_cache.json").write_text(
            json.dumps(codex_cache(codex_entry("first"), codex_entry("second"))),
            encoding="utf-8",
        )
        self.env = {"CODEX_HOME": str(self.home), "HOME": str(self.home)}

    def test_reports_every_known_backend_by_default(self) -> None:
        document = build_inventory(env=self.env)

        self.assertEqual(SCHEMA_VERSION, document["schema_version"])
        self.assertEqual(
            list(BACKEND_NAMES), [b["backend"] for b in document["backends"]]
        )

    def test_restricts_the_report_to_one_backend(self) -> None:
        document = build_inventory(actuator="codex", env=self.env)

        self.assertEqual(["codex"], [b["backend"] for b in document["backends"]])

    def test_restricts_the_report_to_one_model(self) -> None:
        document = build_inventory(actuator="codex", model="second", env=self.env)

        backend = document["backends"][0]

        self.assertEqual(["second"], [m["model"] for m in backend["models"]])

    def test_rejects_an_unknown_backend_or_model(self) -> None:
        with self.assertRaises(ModelSelectionError):
            build_inventory(actuator="gemini", env=self.env)
        with self.assertRaises(ModelSelectionError):
            build_inventory(actuator="codex", model="absent", env=self.env)
        with self.assertRaises(ModelSelectionError):
            build_inventory(model="first", env=self.env)


class ProfileValidationTests(unittest.TestCase):
    """The inventory can contradict a profile; it can never authorize one."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.env = {"CODEX_HOME": str(self.home), "HOME": str(self.home)}
        self.write_cache(
            codex_cache(
                codex_entry("fast-tier"),
                codex_entry("standard-only", additional_speed_tiers=[]),
            )
        )

    def write_cache(self, document) -> None:
        (self.home / "models_cache.json").write_text(
            json.dumps(document), encoding="utf-8"
        )

    def validate(self, **overrides) -> dict:
        request = {
            "backend": "codex",
            "model": "fast-tier",
            "effort": "high",
            "speed": "standard",
            "env": self.env,
        }
        request.update(overrides)
        return validate_profile(**request)

    def test_supports_a_profile_the_inventory_lists_whole(self) -> None:
        profile = self.validate(effort="high", speed="fast")

        self.assertEqual(PROFILE_SUPPORTED, profile["status"])
        self.assertEqual("backend-cache", profile["inventory_source"])
        self.assertIn("fast-tier", profile["reason"])

    def test_supports_an_absent_effort_the_backend_will_default(self) -> None:
        profile = self.validate(effort=None)

        self.assertEqual(PROFILE_SUPPORTED, profile["status"])
        self.assertNotIn("effort", profile["reason"])

    def test_refuses_an_effort_the_listed_model_does_not_declare(self) -> None:
        profile = self.validate(effort="ultra")

        self.assertEqual(PROFILE_UNSUPPORTED, profile["status"])
        self.assertIn("effort ultra", profile["reason"])

    def test_refuses_a_speed_the_listed_model_does_not_declare(self) -> None:
        profile = self.validate(model="standard-only", speed="fast")

        self.assertEqual(PROFILE_UNSUPPORTED, profile["status"])
        self.assertIn("speed fast", profile["reason"])

    def test_leaves_a_model_the_inventory_does_not_list_unverified(self) -> None:
        profile = self.validate(model="absent", effort="ultra", speed="fast")

        self.assertEqual(PROFILE_UNVERIFIED, profile["status"])
        self.assertEqual("backend-cache", profile["inventory_source"])
        self.assertIn("absent", profile["reason"])

    def test_leaves_an_unavailable_inventory_unverified(self) -> None:
        (self.home / "models_cache.json").unlink()

        profile = self.validate(effort="ultra", speed="fast")

        self.assertEqual(PROFILE_UNVERIFIED, profile["status"])
        self.assertEqual("unavailable", profile["inventory_source"])

    def test_leaves_a_backend_without_a_verified_cache_unverified(self) -> None:
        profile = self.validate(backend="claude", model="opus", effort="xhigh")

        self.assertEqual(PROFILE_UNVERIFIED, profile["status"])
        self.assertEqual("unavailable", profile["inventory_source"])
        self.assertIn(CLAUDE_UNVERIFIED_REASON, profile["reason"])

    def test_leaves_an_unrequested_model_unverified(self) -> None:
        profile = self.validate(model=None, effort="ultra")

        self.assertEqual(PROFILE_UNVERIFIED, profile["status"])
        self.assertIn("default", profile["reason"])

    def test_rejects_an_unknown_backend(self) -> None:
        with self.assertRaises(ModelSelectionError):
            self.validate(backend="gemini")

    def test_reports_exactly_the_documented_fields(self) -> None:
        self.assertEqual(
            {"status", "reason", "inventory_source"}, set(self.validate())
        )


class CommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "codex"
        self.home.mkdir()
        (self.home / "models_cache.json").write_text(
            json.dumps(codex_cache(codex_entry("first"), codex_entry("second"))),
            encoding="utf-8",
        )
        self.state_dir = self.root / "state"
        self.env = {
            "CODEX_HOME": str(self.home),
            "HOME": str(self.root),
            "PATH": "",
        }

    def run_command(self, *arguments):
        argv = ["codeservo", "models", "--state-dir", str(self.state_dir), *arguments]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), patch(
            "sys.argv", argv
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                main()
        return raised.exception.code, stdout.getvalue(), stderr.getvalue()

    @property
    def inventory_path(self) -> Path:
        return self.state_dir / "models" / "inventory.json"

    def test_reports_without_starting_any_process(self) -> None:
        def forbidden(*args, **kwargs):
            raise AssertionError("the inventory must run no subprocess")

        with patch.object(subprocess, "run", forbidden), patch.object(
            subprocess, "Popen", forbidden
        ):
            code, stdout, _ = self.run_command("--actuator", "codex", "--json")

        self.assertEqual(0, code)
        self.assertEqual(
            ["first", "second"],
            [m["model"] for m in json.loads(stdout)["backends"][0]["models"]],
        )

    def test_writes_the_document_it_prints(self) -> None:
        code, stdout, _ = self.run_command("--json")

        self.assertEqual(0, code)
        self.assertEqual(
            stdout.encode("utf-8"), self.inventory_path.read_bytes()
        )

    def test_writes_the_same_document_when_it_prints_a_listing(self) -> None:
        listing_code, listing, _ = self.run_command("--actuator", "codex")
        written = json.loads(self.inventory_path.read_text(encoding="utf-8"))
        json_code, printed, _ = self.run_command("--actuator", "codex", "--json")

        self.assertEqual(0, listing_code)
        self.assertEqual(0, json_code)
        self.assertIn("first", listing)
        self.assertIn("advertised", listing)
        self.assertEqual(written["backends"], json.loads(printed)["backends"])

    def test_keeps_a_zero_status_when_a_cache_is_missing(self) -> None:
        (self.home / "models_cache.json").unlink()

        code, stdout, _ = self.run_command("--json")

        self.assertEqual(0, code)
        self.assertEqual(
            ["unavailable", "unavailable"],
            [b["source"] for b in json.loads(stdout)["backends"]],
        )

    def test_rejects_an_unknown_model_with_a_usage_error(self) -> None:
        code, stdout, stderr = self.run_command(
            "--actuator", "codex", "--model", "absent"
        )

        self.assertNotEqual(0, code)
        self.assertEqual("", stdout)
        self.assertIn("absent", stderr)
        self.assertFalse(self.inventory_path.exists())

    def test_rejects_an_unknown_backend_with_a_usage_error(self) -> None:
        code, _, _ = self.run_command("--actuator", "gemini")

        self.assertNotEqual(0, code)


if __name__ == "__main__":
    unittest.main()
