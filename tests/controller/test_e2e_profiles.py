"""The inference profile each role was given, what reached its backend, and what it cost."""

import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeservo.actuators import claude_code, codex
from codeservo.actuators.catalogue import load_catalogue
from codeservo.controller import ControlFailure
from codeservo.evidence.digests import sha256_text
from codeservo.evidence.journal import JOURNAL_NAME, read_journal
from codeservo.evidence.verify import verify_run
from codeservo.runtime.confinement import mechanism
from e2e_support import FAKE_CODEX
from harness import (
    AGENT_COST_USD,
    AGENT_MODEL,
    BILLED_MODEL,
    REQUESTED_EFFORT,
    REQUESTED_MODEL,
    REVIEW_MODEL,
    build_case,
)
from isolation_harness import requires_a_mechanism

# What the fake Codex reviewer bills at gpt-5.6-sol's prices: 200 uncached
# input at 4, 1000 cache reads at 0.4, 300 output at 20, per million.
CODEX_REVIEW_COST_USD = 0.0072


@requires_a_mechanism
class InferenceProfileE2ETests(unittest.TestCase):
    def _codex_case(self, root: Path):
        """A run whose reviewer can be the scripted Codex."""
        case = build_case(root, implementer="implement(ACCEPTABLE)")
        agent = case.bin_dir / "codex"
        agent.write_text(FAKE_CODEX, encoding="utf-8")
        agent.chmod(agent.stat().st_mode | stat.S_IXUSR)
        return case, {
            "CODESERVO_TEST_SOURCE_GIT": str((case.repo / ".git").resolve()),
            "CODESERVO_TEST_SOURCE_REPO": str(case.repo.resolve()),
        }

    def _reviewer_argv(self, run_dir: str) -> list[str]:
        """The command line the fake Codex reviewer reported it was given."""
        reviews = sorted(Path(run_dir, "iterations").glob("*/review/stdout.log"))
        stdout = reviews[-1].read_text(encoding="utf-8")
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
                result = case.run(model="claude-opus-5", effort="high")

            self.assertEqual("ACCEPTED", result["status"])
            requested = {"backend": "claude", "model": "claude-opus-5", "effort": "high"}
            # Both roles exist in the persisted record before anything starts,
            # the reviewer's resolved from the implementer's.
            self.assertEqual({"implementer", "reviewer"}, set(frozen[0]["inference"]))
            self.assertEqual(requested, frozen[0]["inference"]["reviewer"]["requested"])
            before = frozen[0]["inference"]["implementer"]
            self.assertEqual(requested, before["requested"])
            self.assertEqual({"requested", "native", "observed", "provenance"}, set(before))
            self.assertIsNone(before["native"])
            # Before any backend answered, every field is empty and says why.
            self.assertEqual({"model": None, "effort": None}, before["observed"])
            self.assertEqual(
                {"model": "not_reported", "effort": "not_reported"}, before["provenance"]
            )

            after = result["inference"]["implementer"]
            self.assertEqual(before["requested"], after["requested"])
            # The two flags the command carried, unchanged.
            self.assertEqual({"--model": "claude-opus-5", "--effort": "high"}, after["native"])
            # The session named a model of its own: the record holds that one,
            # not the one the request carried.
            self.assertEqual({"model": AGENT_MODEL, "effort": None}, after["observed"])
            self.assertNotEqual(after["requested"]["model"], after["observed"]["model"])
            self.assertEqual(
                {"model": "reported", "effort": "not_reported"}, after["provenance"]
            )

            reviewer = result["inference"]["reviewer"]
            self.assertEqual({"model": REVIEW_MODEL, "effort": None}, reviewer["observed"])
            self.assertEqual(
                {"model": "reported", "effort": "not_reported"}, reviewer["provenance"]
            )
            runtime = result["runtime"]
            self.assertEqual("claude-opus-5", runtime["implementer_model"])
            self.assertEqual("claude-opus-5", runtime["reviewer_model"])

    def test_a_model_the_catalogue_does_not_list_is_refused_before_any_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(ACCEPTABLE)")

            with self.assertRaisesRegex(ControlFailure, "names no claude model 'opus'"):
                case.run(model="opus", effort="high")

            self.assertFalse((case.state_dir / "runs").exists())

    def test_a_model_of_the_other_backend_is_refused_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(ACCEPTABLE)")

            with self.assertRaisesRegex(
                ControlFailure, "implementer profile: gpt-5.6-sol is a codex model"
            ):
                case.run(model="gpt-5.6-sol", effort="high")

            self.assertFalse((case.state_dir / "runs").exists())

    def test_an_effort_outside_the_four_is_refused_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(ACCEPTABLE)")

            with self.assertRaisesRegex(ControlFailure, "reviewer effort must be one of"):
                case.run(review_effort="ultra")

            self.assertFalse((case.state_dir / "runs").exists())

    def test_the_catalogue_is_frozen_with_the_run_and_rates_every_actuation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(ACCEPTABLE)")

            result = case.run()

            self.assertEqual("ACCEPTED", result["status"])
            run_dir = Path(result["run_dir"])
            published = load_catalogue()
            frozen = (run_dir / "catalogue.toml").read_text(encoding="utf-8")
            self.assertEqual(published.raw_text, frozen)
            self.assertEqual(sha256_text(frozen), result["catalogue_sha256"])
            events = {event["type"]: event for event in read_journal(run_dir / JOURNAL_NAME)}
            self.assertEqual(
                result["catalogue_sha256"], events["inputs.frozen"]["payload"]["catalogue_sha256"]
            )
            self.assertEqual(
                {
                    "implementer": {"backend": "claude", "model": REQUESTED_MODEL, "effort": REQUESTED_EFFORT},
                    "reviewer": {"backend": "claude", "model": REQUESTED_MODEL, "effort": REQUESTED_EFFORT},
                },
                events["inference.profiles_frozen"]["payload"],
            )
            report = verify_run(run_dir)
            self.assertEqual("VALID", report["status"])
            self.assertIn("input.catalogue.toml", {check["name"] for check in report["checks"]})

            # The implementer billed under a model the catalogue prices.
            consumed = result["iterations"][-1]["consumption"]
            (item,) = consumed["items"]
            self.assertEqual(BILLED_MODEL, item["model"])
            self.assertEqual("reported_model", item["basis"])
            self.assertEqual(
                {"input": 1000, "cached_input": 2000, "cache_write": 500, "output": 1500, "reasoning": 100},
                item["tokens"],
            )
            self.assertEqual(AGENT_COST_USD, item["cost_usd"])
            self.assertEqual(AGENT_COST_USD, item["reported_cost_usd"])
            self.assertEqual(AGENT_COST_USD, consumed["cost_usd"])
            # The reviewer billed under one it does not: the tokens are kept
            # and the cost stays unknown.
            review = result["iterations"][-1]["review"]["consumption"]
            (item,) = review["items"]
            self.assertEqual(REVIEW_MODEL, item["model"])
            self.assertEqual(40, item["tokens"]["input"])
            self.assertIsNone(item["cost_usd"])
            self.assertIsNone(review["cost_usd"])

    def test_no_review_flag_keeps_one_backend_serving_both_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(ACCEPTABLE)")

            result = case.run(model="claude-opus-5", effort="high")

            self.assertEqual("ACCEPTED", result["status"])
            runtime = result["runtime"]
            self.assertEqual("claude", runtime["actuator"])
            self.assertEqual("claude", runtime["review_actuator"])
            self.assertEqual(
                runtime["actuator_version"], runtime["review_actuator_version"]
            )
            reviewer = result["inference"]["reviewer"]
            self.assertEqual(result["inference"]["implementer"]["requested"], reviewer["requested"])
            self.assertEqual({"--model": "claude-opus-5", "--effort": "high"}, reviewer["native"])
            command = result["iterations"][-1]["review"]["meta"]["command"]
            self.assertEqual("claude-opus-5", command[command.index("--model") + 1])
            self.assertEqual("high", command[command.index("--effort") + 1])
            self.assertNotIn("--settings", command)
            self.assertIn("--safe-mode", command)
            self.assertEqual("json", command[command.index("--output-format") + 1])

    def test_the_reviewer_runs_the_backend_and_profile_it_was_given(self) -> None:
        frozen: list[dict] = []
        review = codex.run_reviewer

        def observe(**arguments):
            # The record already on disk when the reviewer is about to start.
            run_dir = arguments["out_dir"].parents[2]
            frozen.append(
                json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
            )
            return review(**arguments)

        with tempfile.TemporaryDirectory() as temp:
            case, env = self._codex_case(Path(temp))

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
            self.assertEqual(REQUESTED_MODEL, runtime["implementer_model"])
            self.assertEqual("gpt-5.6-sol", runtime["reviewer_model"])

            implementer = result["inference"]["implementer"]
            reviewer = result["inference"]["reviewer"]
            self.assertEqual("claude", implementer["requested"]["backend"])
            self.assertEqual(
                {"backend": "codex", "model": "gpt-5.6-sol", "effort": "high"},
                reviewer["requested"],
            )
            # The flag and the key the reviewer command carried, under the
            # backend's names, and nothing of the implementer's.
            self.assertEqual(
                {"--model": "gpt-5.6-sol", "model_reasoning_effort": "high"},
                reviewer["native"],
            )
            self.assertEqual(
                {"--model": REQUESTED_MODEL, "--effort": REQUESTED_EFFORT},
                implementer["native"],
            )
            # Nothing requested is copied into what the backend reported: the
            # Codex stream names neither, and says so per field.
            self.assertEqual({"model": None, "effort": None}, reviewer["observed"])
            self.assertEqual(
                {"model": "not_reported", "effort": "not_reported"}, reviewer["provenance"]
            )
            self.assertEqual(AGENT_MODEL, implementer["observed"]["model"])
            self.assertEqual("reported", implementer["provenance"]["model"])

            argv = self._reviewer_argv(result["run_dir"])
            self.assertEqual(["codex", "exec"], argv[:2])
            self.assertIn("--ignore-user-config", argv)
            self.assertIn("--output-schema", argv)
            self.assertIn("--json", argv)
            self.assertIn("--output-last-message", argv)
            self.assertEqual("gpt-5.6-sol", argv[argv.index("--model") + 1])
            self.assertEqual(
                ["model_reasoning_effort=high"],
                [argv[index + 1] for index, item in enumerate(argv) if item == "-c"],
            )
            self.assertNotIn("service_tier=priority", argv)

            # Codex names no model: the review's tokens are rated at the one
            # requested, and the record says the attribution is the controller's.
            consumed = result["iterations"][-1]["review"]["consumption"]
            (item,) = consumed["items"]
            self.assertEqual("gpt-5.6-sol", item["model"])
            self.assertEqual("requested_model", item["basis"])
            self.assertEqual(
                {"input": 200, "cached_input": 1000, "cache_write": 0, "output": 300, "reasoning": 50},
                item["tokens"],
            )
            self.assertEqual(CODEX_REVIEW_COST_USD, item["cost_usd"])
            self.assertIsNone(item["reported_cost_usd"])
            self.assertEqual(CODEX_REVIEW_COST_USD, consumed["cost_usd"])

            # The confinement the reviewer runs under, recorded before it ran.
            frozen_review = frozen[0]["iterations"][-1]["review"]
            isolation = frozen_review["isolation"]
            self.assertEqual(mechanism(), isolation["mechanism"])
            self.assertTrue(isolation["user_config_ignored"])
            self.assertNotIn("result", frozen_review)
            self.assertFalse(Path(result["worktree"], "reviewer-write.txt").exists())

    def test_a_reviewer_model_of_the_other_backend_is_refused_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case, env = self._codex_case(Path(temp))

            with self.assertRaisesRegex(
                ControlFailure, "reviewer profile: claude-opus-5 is a claude model"
            ):
                case.run(
                    env=env,
                    actuator="claude",
                    review_actuator="codex",
                    review_model="claude-opus-5",
                    review_effort="high",
                )

            self.assertFalse((case.state_dir / "runs").exists())


if __name__ == "__main__":
    unittest.main()
