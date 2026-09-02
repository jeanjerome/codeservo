"""One accepted run, asserted from the first actuation to the decision."""

import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from codeservo.evidence.digests import sha256_text

from e2e_support import FAKE_AGENTS, canonical
from harness import Case, TASK_TEXT, commit_repository, constitution


@unittest.skipUnless(
    sys.platform == "darwin",
    "controller confinement requires macOS sandbox-exec",
)
class AcceptedRunTests(unittest.TestCase):
    def test_feedback_loop_converges_and_accepts(self) -> None:
        for actuator in sorted(FAKE_AGENTS):
            with self.subTest(actuator=actuator):
                self._assert_converges(actuator)

    def _assert_converges(self, actuator: str) -> None:
        binary_name, script, version = FAKE_AGENTS[actuator]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            state_dir = root / "state"
            bin_dir = root / "bin"
            repo.mkdir()
            bin_dir.mkdir()
            (repo / ".codeservo").mkdir()
            sensor = state_dir / "sensors" / "test" / "task-outcome"
            sensor.mkdir(parents=True)
            (sensor / "README.md").write_text(
                "Controller-owned test sensor.\n", encoding="utf-8"
            )

            (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
            (repo / "app.py").write_text("def value():\n    return 0\n", encoding="utf-8")
            historical_sensor = repo / "historical-sensor.txt"
            historical_sensor.write_text("must stay hidden\n", encoding="utf-8")
            (repo / ".codeservo" / "constitution.toml").write_text(
                constitution(), encoding="utf-8"
            )
            task = root / "TASK.md"
            task.write_text(TASK_TEXT, encoding="utf-8")

            fake_agent = bin_dir / binary_name
            fake_agent.write_text(script, encoding="utf-8")
            fake_agent.chmod(fake_agent.stat().st_mode | stat.S_IXUSR)

            commit_repository(repo, "historical sensor")
            historical_blob = subprocess.run(
                ["git", "rev-parse", "HEAD:historical-sensor.txt"],
                cwd=repo,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            historical_sensor.unlink()
            commit_repository(repo, "clean baseline")

            case = Case(
                root=root,
                repo=repo,
                state_dir=state_dir,
                task=task,
                bin_dir=bin_dir,
            )
            result = case.run(
                env={
                    "CODESERVO_TEST_SOURCE_GIT": str((repo / ".git").resolve()),
                    "CODESERVO_TEST_SOURCE_REPO": str(repo.resolve()),
                    # No local cache, so the profile stays unverified whatever
                    # this machine happens to hold.
                    "CODEX_HOME": str(root / "absent-codex"),
                },
                actuator=actuator,
                max_iterations=3,
                effort="high",
                speed="fast",
            )

            self.assertEqual("ACCEPTED", result["status"])
            self.assertEqual(str(state_dir.resolve()), result["state_dir"])
            self.assertTrue(Path(result["run_dir"]).is_relative_to(state_dir.resolve()))
            self.assertTrue(Path(result["worktree"]).is_relative_to(state_dir.resolve()))
            self.assertFalse((repo / "actuator-write.txt").exists())
            self.assertFalse(Path(result["worktree"], "reviewer-write.txt").exists())
            self.assertEqual(2, len(result["iterations"]))
            first, second = result["iterations"]
            self.assertFalse(first["quick_gates"][1]["passed"])
            self.assertTrue(second["quick_gates"][1]["passed"])
            self.assertEqual("", first["feedback_received"])
            self.assertIn("Gate task-outcome FAILED", first["controller_feedback"]["text"])
            self.assertEqual(
                first["controller_feedback"]["text"],
                Path(first["controller_feedback"]["path"]).read_text(encoding="utf-8"),
            )
            self.assertEqual(
                first["controller_feedback"]["text"], second["feedback_received"]
            )
            self.assertEqual(
                first["observed_state"]["sha256"], second["input_state"]["sha256"]
            )
            self.assertNotEqual(
                first["input_state"]["sha256"], first["actuator_state"]["sha256"]
            )
            second_prompt = Path(second["prompt"]["path"]).read_text(encoding="utf-8")
            self.assertIn(first["controller_feedback"]["text"], second_prompt)
            for iteration in (first, second):
                for state_name in ("input_state", "actuator_state", "observed_state"):
                    self.assertTrue(Path(iteration[state_name]["path"]).is_file())
            self.assertTrue(Path(result["run_dir"], "change.patch").is_file())
            shallow_count = subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                cwd=result["worktree"],
                text=True,
                capture_output=True,
                check=True,
            )
            remotes = subprocess.run(
                ["git", "remote"],
                cwd=result["worktree"],
                text=True,
                capture_output=True,
                check=True,
            )
            historical_object = subprocess.run(
                ["git", "cat-file", "-e", historical_blob],
                cwd=result["worktree"],
                capture_output=True,
                check=False,
            )
            self.assertEqual("1", shallow_count.stdout.strip())
            self.assertEqual("", remotes.stdout.strip())
            self.assertNotEqual(0, historical_object.returncode)
            evidence = json.loads(Path(result["run_dir"], "evidence.json").read_text())
            self.assertEqual(16, evidence["schema_version"])
            # A constitution declaring no provider keeps shell gates.
            self.assertEqual({"provider": "none"}, evidence["environment"])
            self.assertEqual(".", evidence["run_dir"])
            self.assertFalse(Path(evidence["state_dir"]).is_absolute())
            self.assertFalse(Path(evidence["worktree"]).is_absolute())
            self.assertEqual(actuator, evidence["runtime"]["actuator"])
            self.assertEqual(version, evidence["runtime"]["actuator_version"])
            frozen_sensor = evidence["sensors"]["task-outcome"]
            frozen_sensor_path = Path(result["run_dir"], frozen_sensor["path"])
            self.assertTrue(frozen_sensor_path.is_dir())
            self.assertTrue(Path(frozen_sensor_path, "README.md").is_file())
            isolation = evidence["actuator_isolation"]
            self.assertEqual("macos-sandbox-exec", isolation["mechanism"])
            self.assertIn("../../../sensors", isolation["denied_paths"])
            self.assertTrue(isolation["read_only_paths"])
            self.assertTrue(
                all(
                    not Path(path).is_absolute()
                    for path in isolation["denied_paths"] + isolation["read_only_paths"]
                )
            )
            self._assert_confinements(result, evidence)
            self.assertEqual(
                {"path", "sha256"}, set(evidence["full_gate_state"])
            )
            self.assertEqual("full.patch", evidence["full_gate_state"]["path"])
            # Nothing moved between the quick phase and the full one.
            self.assertEqual(
                second["observed_state"]["sha256"],
                evidence["full_gate_state"]["sha256"],
            )
            self.assertTrue(Path(result["run_dir"], "full.patch").is_file())
            for gate in evidence["baseline"] + evidence["full_gates"]:
                self.assertEqual(64, len(gate["stdout_sha256"]))
                self.assertEqual(64, len(gate["stderr_sha256"]))
                self.assertEqual(64, len(gate["result_sha256"]))
            if actuator == "claude":
                models = evidence["iterations"][0]["agent"]["models"]
                self.assertEqual("test-model", models["session_model"])
                self.assertEqual(12, models["usage"]["test-model"]["output_tokens"])
            for iteration in evidence["iterations"]:
                self.assertEqual(64, len(iteration["agent"]["events_sha256"]))
                self.assertEqual(64, len(iteration["agent"]["result_sha256"]))
            self._assert_inference(actuator, evidence)
            self.assertEqual("ACCEPTED", evidence["status"])
            self._assert_observations(result, evidence)

    def _assert_confinements(self, result: dict, evidence: dict) -> None:
        """One confinement per measured tree, on a run declaring no provider."""
        repo = Path(result["repo"])
        worktree = Path(result["worktree"])
        metadata = str(worktree / ".git")

        for document in evidence["gate_isolation"].values():
            self.assertEqual("macos-sandbox-exec", document["mechanism"])
            self.assertEqual([], document["denied_paths"])
            self.assertTrue(document["user_config_ignored"])
            self.assertEqual(".", document["read_only_paths"][0])
            self.assertTrue(
                all(
                    not Path(path).is_absolute()
                    for path in document["read_only_paths"]
                )
            )
        # Each phase measures one tree and is handed that tree's paths only.
        # Declaring no provider names no provider directory anywhere.
        self.assertEqual(
            [result["run_dir"], str(repo / ".git")],
            result["gate_isolation"]["source"]["read_only_paths"],
        )
        self.assertEqual(
            [result["run_dir"], metadata],
            result["gate_isolation"]["candidate"]["read_only_paths"],
        )
        # The actuator reads the candidate's metadata and cannot write it.
        actuator = result["actuator_isolation"]
        self.assertEqual([str(repo), metadata], actuator["read_only_paths"])
        self.assertNotIn(metadata, actuator["denied_paths"])
        # The reviewer's confinement is unchanged: the whole worktree.
        self.assertEqual(
            [str(repo), str(worktree)],
            result["review"]["isolation"]["read_only_paths"],
        )

    def _assert_inference(self, actuator: str, evidence: dict) -> None:
        """The profile the run requested, sent and observed, for one backend."""
        implementer = evidence["inference"]["implementer"]
        reviewer = evidence["inference"]["reviewer"]

        # No review flag was given: the implementer backend serves both roles
        # on the documented defaults, and carries none of its own profile.
        self.assertEqual(actuator, evidence["runtime"]["review_actuator"])
        self.assertEqual(
            evidence["runtime"]["actuator_version"],
            evidence["runtime"]["review_actuator_version"],
        )
        self.assertEqual(
            {
                "backend": actuator,
                "model": None,
                "effort": None,
                "speed": "standard",
            },
            reviewer["requested"],
        )
        self.assertEqual("unverified", reviewer["validation"]["status"])
        self.assertEqual({}, reviewer["native"])
        if actuator == "claude":
            # No init event: the model comes from what the session billed.
            self.assertEqual(
                {
                    "model": "test-review-model",
                    "effort": None,
                    "speed": "standard",
                },
                reviewer["observed"],
            )
        else:
            self.assertEqual(
                {"model": None, "effort": None, "speed": None}, reviewer["observed"]
            )
        self._assert_agrees(reviewer)

        self.assertEqual(
            {
                "backend": actuator,
                "model": None,
                "effort": "high",
                "speed": "fast",
            },
            implementer["requested"],
        )
        self.assertEqual("unverified", implementer["validation"]["status"])
        if actuator == "claude":
            self.assertEqual(
                {"--effort": "high", "--settings": {"fastMode": True}},
                implementer["native"],
            )
            # Reported: the model of the init event, the speed of the result.
            self.assertEqual(
                {"model": "test-model", "effort": None, "speed": "standard"},
                implementer["observed"],
            )
        else:
            self.assertEqual(
                {"model_reasoning_effort": "high", "service_tier": "priority"},
                implementer["native"],
            )
            # The stream of the installed Codex names none of the three, and
            # the fast tier it was sent is not read back off the command line.
            self.assertEqual(
                {"model": None, "effort": None, "speed": None},
                implementer["observed"],
            )
        self._assert_agrees(implementer)
        # No backend reports a reasoning effort, and neither borrows the one
        # the request carried.
        self.assertIsNone(implementer["observed"]["effort"])
        self.assertEqual("high", implementer["requested"]["effort"])
        # Neither field survives from an earlier iteration of the same run.
        last = evidence["iterations"][-1]["agent"]
        self.assertEqual(2, len(evidence["iterations"]))
        self.assertEqual(last["native"], implementer["native"])
        self.assertEqual(last["observed"], implementer["observed"])

    def _assert_agrees(self, profile: dict) -> None:
        """`observed` and `provenance` name the same fields and never disagree."""
        self.assertEqual({"model", "effort", "speed"}, set(profile["observed"]))
        self.assertEqual(set(profile["observed"]), set(profile["provenance"]))
        for name, value in profile["observed"].items():
            self.assertEqual(
                "reported" if value is not None else "not_reported",
                profile["provenance"][name],
                name,
            )

    def _assert_observations(self, result: dict, evidence: dict) -> None:
        """The reviewer received exactly the bundle the controller recorded."""
        review_prompt = Path(result["run_dir"], "review", "prompt.md").read_text(
            encoding="utf-8"
        )
        after = review_prompt.partition("BEGIN CONTROLLER OBSERVATIONS JSON\n")[2]
        embedded = after.partition("\nEND CONTROLLER OBSERVATIONS JSON")[0]
        observations = evidence["review"]["observations"]

        self.assertEqual(observations, json.loads(embedded))
        self.assertEqual(canonical(observations), embedded)
        # The recorded digest covers exactly the bytes the reviewer was given.
        self.assertEqual(
            sha256_text(embedded), evidence["review"]["observations_sha256"]
        )
        self.assertEqual(1, observations["schema_version"])
        self.assertEqual(
            [
                ("quick", "syntax", "repository_gate", None),
                ("quick", "task-outcome", "external_sensor", "test/task-outcome"),
                ("full", "full", "repository_gate", None),
            ],
            [
                (gate["phase"], gate["name"], gate["kind"], gate["sensor"])
                for gate in observations["gates"]
            ],
        )
        for gate in observations["gates"]:
            self.assertTrue(gate["passed"])
            self.assertEqual(0, gate["exit_code"])
            self.assertFalse(gate["timed_out"])
            self.assertEqual(64, len(gate["result_sha256"]))
            self.assertEqual([], [key for key in gate if key.endswith("_path")])
            self.assertLessEqual(len(gate["stdout_tail"].splitlines()), 120)
            self.assertLessEqual(len(gate["stderr_tail"].splitlines()), 120)

        serialized = json.dumps(observations)
        self.assertNotIn(result["run_dir"], serialized)
        self.assertNotIn(result["worktree"], serialized)
        self.assertNotIn("SENSOR_PATH", serialized)

        for iteration in evidence["iterations"]:
            implementer = Path(
                result["run_dir"], iteration["prompt"]["path"]
            ).read_text(encoding="utf-8")
            self.assertNotIn("CONTROLLER OBSERVATIONS", implementer)
            self.assertNotIn("test/task-outcome", implementer)
            self.assertNotIn("stdout_tail", implementer)


if __name__ == "__main__":
    unittest.main()
