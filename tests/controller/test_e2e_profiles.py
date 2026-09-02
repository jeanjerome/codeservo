"""The inference profile each role was given, and what reached its backend."""

import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeservo.actuators import claude_code, codex

from e2e_support import FAKE_CODEX, codex_cache
from harness import AGENT_MODEL, AGENT_SPEED, REVIEW_MODEL, build_case


@unittest.skipUnless(
    sys.platform == "darwin",
    "controller confinement requires macOS sandbox-exec",
)
class InferenceProfileE2ETests(unittest.TestCase):
    def _codex_case(self, root: Path, *, efforts: list[str], fast: bool):
        """A run whose backend has a local inventory the controller can read."""
        case = build_case(root, implementer="implement(ACCEPTABLE)")
        agent = case.bin_dir / "codex"
        agent.write_text(FAKE_CODEX, encoding="utf-8")
        agent.chmod(agent.stat().st_mode | stat.S_IXUSR)
        codex_home = root / "codex"
        codex_home.mkdir()
        (codex_home / "models_cache.json").write_text(
            codex_cache("gpt-5.6-sol", efforts, fast), encoding="utf-8"
        )
        return case, {
            "CODEX_HOME": str(codex_home),
            "CODESERVO_TEST_SOURCE_GIT": str((case.repo / ".git").resolve()),
            "CODESERVO_TEST_SOURCE_REPO": str(case.repo.resolve()),
        }

    def _reviewer_argv(self, run_dir: str) -> list[str]:
        """The command line the fake Codex reviewer reported it was given."""
        stdout = Path(run_dir, "review", "stdout.log").read_text(encoding="utf-8")
        for line in stdout.splitlines():
            payload = json.loads(line)
            if "argv" in payload:
                return ["codex", *payload["argv"]]
        raise AssertionError(f"the reviewer reported no command line: {stdout}")

    def test_records_the_requested_profile_before_the_first_actuation(self) -> None:
        frozen: list[dict] = []
        actuate = claude_code.run_implementer

        def observe(**arguments):
            # The record already on disk when the actuator is about to start.
            run_dir = arguments["out_dir"].parents[2]
            frozen.append(
                json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
            )
            return actuate(**arguments)

        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(ACCEPTABLE)")

            with patch.object(claude_code, "run_implementer", observe):
                result = case.run(model="opus", effort="high", speed="fast")

            self.assertEqual("ACCEPTED", result["status"])
            # Both roles exist in the persisted record before anything starts.
            self.assertEqual({"implementer", "reviewer"}, set(frozen[0]["inference"]))
            self.assertEqual(
                {
                    "backend": "claude",
                    "model": None,
                    "effort": None,
                    "speed": "standard",
                },
                frozen[0]["inference"]["reviewer"]["requested"],
            )
            before = frozen[0]["inference"]["implementer"]
            self.assertEqual(
                {
                    "backend": "claude",
                    "model": "opus",
                    "effort": "high",
                    "speed": "fast",
                },
                before["requested"],
            )
            self.assertEqual("unverified", before["validation"]["status"])
            self.assertIsNone(before["native"])
            # Before any backend answered, every field is empty and says why.
            self.assertEqual(
                {"model": None, "effort": None, "speed": None}, before["observed"]
            )
            self.assertEqual(
                {
                    "model": "not_reported",
                    "effort": "not_reported",
                    "speed": "not_reported",
                },
                before["provenance"],
            )

            after = result["inference"]["implementer"]
            self.assertEqual(before["requested"], after["requested"])
            # What the settings document holds, not the path it briefly had.
            self.assertEqual(
                {"--effort": "high", "--settings": {"fastMode": True}},
                after["native"],
            )
            # The session named a model of its own: the record holds that one,
            # not the alias the request carried.
            self.assertEqual(
                {
                    "model": AGENT_MODEL,
                    "effort": None,
                    "speed": AGENT_SPEED,
                },
                after["observed"],
            )
            self.assertNotEqual(after["requested"]["model"], after["observed"]["model"])
            self.assertEqual(
                {
                    "model": "reported",
                    "effort": "not_reported",
                    "speed": "reported",
                },
                after["provenance"],
            )
            # The requested effort reached the command line and no observation.
            self.assertEqual("high", after["requested"]["effort"])
            self.assertEqual("high", after["native"]["--effort"])

            reviewer = result["inference"]["reviewer"]
            self.assertEqual(
                {
                    "model": REVIEW_MODEL,
                    "effort": None,
                    "speed": AGENT_SPEED,
                },
                reviewer["observed"],
            )
            self.assertEqual(
                {
                    "model": "reported",
                    "effort": "not_reported",
                    "speed": "reported",
                },
                reviewer["provenance"],
            )

    def test_an_unsupported_profile_ends_the_run_before_any_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case, env = self._codex_case(
                Path(temp), efforts=["low", "medium", "high"], fast=False
            )

            result = case.run(
                env=env,
                actuator="codex",
                model="gpt-5.6-sol",
                effort="ultra",
                speed="fast",
            )

            self.assertEqual("REJECTED", result["status"])
            self.assertIsNone(result["worktree"])
            self.assertEqual([], result["iterations"])
            self.assertNotIn("baseline", result)
            self.assertFalse(Path(result["run_dir"], "iterations").exists())
            self.assertEqual(
                1, len(result["decision"]["reasons"]), result["decision"]["reasons"]
            )
            self.assertIn("configuration error", result["decision"]["reasons"][0])
            self.assertIn("implementer", result["decision"]["reasons"][0])

            evidence = json.loads(
                Path(result["run_dir"], "evidence.json").read_text(encoding="utf-8")
            )
            implementer = evidence["inference"]["implementer"]
            validation = implementer["validation"]
            self.assertEqual("unsupported", validation["status"])
            self.assertEqual("backend-cache", validation["inventory_source"])
            self.assertIn("effort ultra", validation["reason"])
            self.assertIn("speed fast", validation["reason"])
            self.assertEqual("ultra", implementer["requested"]["effort"])
            self.assertIsNone(implementer["native"])
            self.assertIsNone(evidence["worktree"])

    def test_a_supported_profile_reaches_the_actuator(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case, env = self._codex_case(
                Path(temp), efforts=["low", "medium", "high"], fast=True
            )

            result = case.run(
                env=env,
                actuator="codex",
                model="gpt-5.6-sol",
                effort="high",
                speed="fast",
                max_iterations=3,
            )

            self.assertEqual("ACCEPTED", result["status"])
            implementer = result["inference"]["implementer"]
            self.assertEqual("supported", implementer["validation"]["status"])
            self.assertEqual(
                "backend-cache", implementer["validation"]["inventory_source"]
            )
            self.assertIsNotNone(result["worktree"])
            self.assertEqual(
                {"model_reasoning_effort": "high", "service_tier": "priority"},
                implementer["native"],
            )

    def test_no_review_flag_keeps_one_backend_serving_both_roles(self) -> None:
        """The documented defaults reproduce the behaviour of an earlier run."""
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(ACCEPTABLE)")

            result = case.run(model="opus", effort="high", speed="fast")

            self.assertEqual("ACCEPTED", result["status"])
            runtime = result["runtime"]
            self.assertEqual("claude", runtime["actuator"])
            self.assertEqual("claude", runtime["review_actuator"])
            self.assertEqual(
                runtime["actuator_version"], runtime["review_actuator_version"]
            )
            self.assertEqual("opus", runtime["implementer_model"])
            self.assertEqual("claude-default", runtime["reviewer_model"])

            reviewer = result["inference"]["reviewer"]
            self.assertEqual(
                {
                    "backend": "claude",
                    "model": None,
                    "effort": None,
                    "speed": "standard",
                },
                reviewer["requested"],
            )
            # No effort, no settings document, no configuration override.
            self.assertEqual({}, reviewer["native"])
            command = result["review"]["meta"]["command"]
            self.assertNotIn("--effort", command)
            self.assertNotIn("--settings", command)
            self.assertNotIn("--model", command)
            self.assertIn("--safe-mode", command)
            self.assertEqual("json", command[command.index("--output-format") + 1])

    def test_the_reviewer_runs_the_backend_and_profile_it_was_given(self) -> None:
        frozen: list[dict] = []
        review = codex.run_reviewer

        def observe(**arguments):
            # The record already on disk when the reviewer is about to start.
            run_dir = arguments["out_dir"].parent
            frozen.append(
                json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
            )
            return review(**arguments)

        with tempfile.TemporaryDirectory() as temp:
            case, env = self._codex_case(
                Path(temp), efforts=["low", "medium", "high"], fast=False
            )

            with patch.object(codex, "run_reviewer", observe):
                result = case.run(
                    env=env,
                    actuator="claude",
                    review_actuator="codex",
                    review_model="gpt-5.6-sol",
                    review_effort="high",
                )

            self.assertEqual("ACCEPTED", result["status"])
            runtime = result["runtime"]
            self.assertEqual("claude", runtime["actuator"])
            self.assertEqual("0.0-test (Claude Code)", runtime["actuator_version"])
            self.assertEqual("codex", runtime["review_actuator"])
            self.assertEqual("codex-cli 0.0-test", runtime["review_actuator_version"])
            self.assertEqual("claude-default", runtime["implementer_model"])
            self.assertEqual("gpt-5.6-sol", runtime["reviewer_model"])

            implementer = result["inference"]["implementer"]
            reviewer = result["inference"]["reviewer"]
            # One backend's inventory never answers for the other's.
            self.assertEqual("claude", implementer["requested"]["backend"])
            self.assertEqual("unverified", implementer["validation"]["status"])
            self.assertEqual(
                {
                    "backend": "codex",
                    "model": "gpt-5.6-sol",
                    "effort": "high",
                    "speed": "standard",
                },
                reviewer["requested"],
            )
            self.assertEqual("supported", reviewer["validation"]["status"])
            self.assertEqual(
                "backend-cache", reviewer["validation"]["inventory_source"]
            )
            # The keys the reviewer command carried, under the backend's names.
            self.assertEqual({"model_reasoning_effort": "high"}, reviewer["native"])
            # The reviewer effort reached the reviewer alone.
            self.assertEqual({}, implementer["native"])
            # Nothing requested is copied into what the backend reported: the
            # Codex stream names none of the three, and says so per field.
            self.assertEqual(
                {"model": None, "effort": None, "speed": None}, reviewer["observed"]
            )
            self.assertEqual(
                {
                    "model": "not_reported",
                    "effort": "not_reported",
                    "speed": "not_reported",
                },
                reviewer["provenance"],
            )
            # The implementer of the other backend still reported its own.
            self.assertEqual(AGENT_MODEL, implementer["observed"]["model"])
            self.assertEqual("reported", implementer["provenance"]["model"])

            argv = self._reviewer_argv(result["run_dir"])
            self.assertEqual(["codex", "exec"], argv[:2])
            self.assertIn("--ignore-user-config", argv)
            self.assertIn("--output-schema", argv)
            # The reviewer reads the same documented event stream as the
            # implementer, and still answers in the file it is given.
            self.assertIn("--json", argv)
            self.assertIn("--output-last-message", argv)
            self.assertEqual(
                ["model_reasoning_effort=high"],
                [argv[index + 1] for index, item in enumerate(argv) if item == "-c"],
            )
            self.assertNotIn("service_tier=priority", argv)

            # The confinement the reviewer runs under, recorded before it ran.
            isolation = frozen[0]["review"]["isolation"]
            self.assertEqual("macos-sandbox-exec", isolation["mechanism"])
            self.assertTrue(isolation["user_config_ignored"])
            self.assertEqual(
                set(result["review"]["isolation"]),
                {"mechanism", "denied_paths", "read_only_paths", "user_config_ignored"},
            )
            self.assertIn(
                result["worktree"], result["review"]["isolation"]["read_only_paths"]
            )
            self.assertNotIn("result", frozen[0]["review"])
            # The candidate is unchanged by a reviewer that could not write.
            self.assertFalse(Path(result["worktree"], "reviewer-write.txt").exists())

    def test_an_unsupported_reviewer_profile_ends_the_run_before_any_checkout(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case, env = self._codex_case(
                Path(temp), efforts=["low", "medium", "high"], fast=False
            )

            result = case.run(
                env=env,
                actuator="claude",
                review_actuator="codex",
                review_model="gpt-5.6-sol",
                review_effort="ultra",
            )

            self.assertEqual("REJECTED", result["status"])
            self.assertIsNone(result["worktree"])
            self.assertEqual([], result["iterations"])
            self.assertNotIn("baseline", result)
            self.assertNotIn("review", result)
            self.assertFalse(Path(result["run_dir"], "iterations").exists())
            self.assertEqual(1, len(result["decision"]["reasons"]))
            reason = result["decision"]["reasons"][0]
            self.assertIn("configuration error", reason)
            self.assertIn("reviewer", reason)

            evidence = json.loads(
                Path(result["run_dir"], "evidence.json").read_text(encoding="utf-8")
            )
            reviewer = evidence["inference"]["reviewer"]
            self.assertEqual("unsupported", reviewer["validation"]["status"])
            self.assertEqual(
                "backend-cache", reviewer["validation"]["inventory_source"]
            )
            self.assertIn("effort ultra", reviewer["validation"]["reason"])
            self.assertIsNone(reviewer["native"])
            # The implementer profile is untouched by the refusal of the other.
            implementer = evidence["inference"]["implementer"]
            self.assertEqual("unverified", implementer["validation"]["status"])
            self.assertIsNone(evidence["worktree"])


if __name__ == "__main__":
    unittest.main()
