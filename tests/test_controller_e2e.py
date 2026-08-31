import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeservo import claude_code
from codeservo.evidence import sha256_text
from harness import TASK_TEXT, Case, build_case, commit_repository, constitution


def codex_cache(model: str, efforts: list[str], fast: bool) -> str:
    """A local Codex cache shaped like the one the `models` command projects."""
    return json.dumps(
        {
            "fetched_at": "2026-08-31T21:49:12.689027Z",
            "client_version": "0.151.0",
            "models": [
                {
                    "slug": model,
                    "display_name": model.upper(),
                    "supported_reasoning_levels": [
                        {"effort": effort} for effort in efforts
                    ],
                    "visibility": "list",
                    "additional_speed_tiers": ["fast"] if fast else [],
                }
            ],
        }
    )


def canonical(payload: dict) -> str:
    """The canonical JSON the controller is expected to prompt and hash."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


ISOLATION_PROBE = '''
def probe_isolation(worktree):
    """Fail loudly when the controller-owned confinement is not in force."""
    source_git = subprocess.run(
        [
            "git",
            f"--git-dir={os.environ['CODESERVO_TEST_SOURCE_GIT']}",
            "show",
            "HEAD^:historical-sensor.txt",
        ],
        capture_output=True,
        check=False,
    )
    if source_git.returncode == 0:
        sys.stderr.write("source repository history is readable")
        raise SystemExit(8)
    worktree_history = subprocess.run(
        ["git", "show", "HEAD^:historical-sensor.txt"],
        cwd=worktree,
        capture_output=True,
        check=False,
    )
    if worktree_history.returncode == 0:
        sys.stderr.write("historical sensor is readable")
        raise SystemExit(9)
    try:
        source = pathlib.Path(os.environ["CODESERVO_TEST_SOURCE_REPO"])
        (source / "actuator-write.txt").write_text("written", encoding="utf-8")
    except OSError:
        pass
    else:
        sys.stderr.write("source repository is writable")
        raise SystemExit(10)


def next_implementation(worktree):
    app = worktree / "app.py"
    app.write_text(
        "def value():\\n    return 1\\n"
        if "return 0" in app.read_text()
        else "def value():\\n    return 2\\n"
    )


REVIEW = {
    "criteria": [
        {"id": "AC1", "status": "satisfied", "evidence": "app.py returns 2"}
    ],
    "findings": [],
}


def probe_read_only(worktree):
    try:
        (worktree / "reviewer-write.txt").write_text("written", encoding="utf-8")
    except OSError:
        return
    sys.stderr.write("reviewer can write to the candidate worktree")
    raise SystemExit(11)


def probe_gate_record(worktree):
    record = pathlib.Path(os.environ["CODESERVO_TEST_GATE_RECORD"])
    try:
        next(record.iterdir())
    except (OSError, StopIteration):
        sys.stderr.write("gate record is not readable")
        raise SystemExit(12)
    probe = record / "actuator-write-{}.txt".format(os.getpid())
    try:
        probe.write_text("written", encoding="utf-8")
    except OSError:
        pass
    else:
        probe.unlink()
        sys.stderr.write("gate record is writable")
        raise SystemExit(13)
    worktree_history = subprocess.run(
        ["git", "show", "HEAD^:historical-sensor.txt"],
        cwd=worktree,
        capture_output=True,
        check=False,
    )
    if worktree_history.returncode == 0:
        sys.stderr.write("historical sensor is readable")
        raise SystemExit(14)


def use_gate_record_probe():
    if os.environ.get("CODESERVO_TEST_NESTED_SEATBELT") != "1":
        return False
    nested = subprocess.run(
        [
            "/usr/bin/sandbox-exec",
            "-p",
            "(version 1)(allow default)",
            "/usr/bin/true",
        ],
        capture_output=True,
        check=False,
    )
    if nested.returncode != os.EX_OSERR:
        sys.stderr.write("confined test mode requested without an outer seatbelt")
        raise SystemExit(15)
    return True


def probe_implementer_isolation(worktree):
    if use_gate_record_probe():
        probe_gate_record(worktree)
    else:
        probe_isolation(worktree)


def probe_reviewer_isolation(worktree):
    if use_gate_record_probe():
        probe_gate_record(worktree)
    else:
        probe_read_only(worktree)
'''

FAKE_CODEX = f'''#!/usr/bin/env python3
import json
import os
import pathlib
import subprocess
import sys

args = sys.argv[1:]
if "--version" in args:
    print("codex-cli 0.0-test")
    raise SystemExit(0)


def value(flag):
    return args[args.index(flag) + 1]

{ISOLATION_PROBE}

worktree = pathlib.Path(value("--cd"))
out = pathlib.Path(value("--output-last-message"))
out.parent.mkdir(parents=True, exist_ok=True)
sys.stdin.read()
if "--output-schema" in args:
    probe_reviewer_isolation(worktree)
    out.write_text(json.dumps(REVIEW))
else:
    probe_implementer_isolation(worktree)
    next_implementation(worktree)
    out.write_text("implemented")
    print(json.dumps({{"type": "message", "message": "done"}}))
'''

FAKE_CLAUDE = f'''#!/usr/bin/env python3
import json
import os
import pathlib
import subprocess
import sys

args = sys.argv[1:]
if "--version" in args:
    print("0.0-test (Claude Code)")
    raise SystemExit(0)


def value(flag):
    return args[args.index(flag) + 1]

{ISOLATION_PROBE}

worktree = pathlib.Path.cwd()
sys.stdin.read()
if value("--output-format") == "json":
    probe_reviewer_isolation(worktree)
    json.dump(
        {{
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": 2,
            "session_id": "review-session",
            "result": json.dumps(REVIEW),
            "structured_output": REVIEW,
        }},
        sys.stdout,
    )
else:
    probe_implementer_isolation(worktree)
    next_implementation(worktree)
    print(json.dumps({{"type": "system", "subtype": "init", "model": "test-model"}}))
    print(
        json.dumps(
            {{
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "num_turns": 3,
                "session_id": "agent-session",
                "total_cost_usd": 0.0,
                "terminal_reason": "completed",
                "result": "implemented",
                "modelUsage": {{"test-model": {{"outputTokens": 12, "costUSD": 0.0}}}},
            }}
        )
    )
'''

FAKE_AGENTS = {
    "codex": ("codex", FAKE_CODEX, "codex-cli 0.0-test"),
    "claude": ("claude", FAKE_CLAUDE, "0.0-test (Claude Code)"),
}


@unittest.skipUnless(
    sys.platform == "darwin",
    "external sensor isolation requires macOS sandbox-exec",
)
class ControllerE2ETests(unittest.TestCase):
    def test_feedback_loop_converges_and_accepts(self) -> None:
        for actuator in sorted(FAKE_AGENTS):
            with self.subTest(actuator=actuator):
                self._assert_converges(actuator)

    def test_bounds_gate_observations_and_hides_controller_locations(self) -> None:
        chatty_sensor = (
            "for i in $(seq 1 300); do echo \"line $i\"; done; "
            "echo \"sensor at $CODESERVO_SENSOR_PATH\"; "
            "grep -q \"return 2\" app.py"
        )
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                constitution_text=constitution(sensor_command=chatty_sensor),
            )

            result = case.run()

            self.assertEqual("ACCEPTED", result["status"])
            prompt = Path(result["run_dir"], "review", "prompt.md").read_text(
                encoding="utf-8"
            )
            observed = {
                gate["name"]: gate
                for gate in result["review"]["observations"]["gates"]
            }
            emitted = observed["task-outcome"]["stdout_tail"].splitlines()

            self.assertEqual(120, len(emitted))
            self.assertEqual("line 182", emitted[0])
            self.assertEqual("sensor at <redacted>/sensors/task-outcome", emitted[-1])
            self.assertNotIn(result["run_dir"], prompt)
            self.assertNotIn(result["worktree"], prompt)
            self.assertNotIn("line 181", prompt)

    def test_records_the_observations_before_the_reviewer_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                reviewer="raise SystemExit(4)",
            )

            result = case.run()

            self.assertEqual("REJECTED", result["status"])
            evidence = json.loads(
                Path(result["run_dir"], "evidence.json").read_text(encoding="utf-8")
            )
            review = evidence["review"]
            # The reviewer failed after it was handed the bundle, which cannot
            # erase what it received.
            self.assertNotIn("result", review)
            self.assertEqual(64, len(review["prompt"]["sha256"]))
            self.assertEqual(
                ["syntax", "task-outcome", "full"],
                [gate["name"] for gate in review["observations"]["gates"]],
            )
            self.assertEqual(
                sha256_text(canonical(review["observations"])),
                review["observations_sha256"],
            )

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
            self.assertEqual(
                {"model": None, "effort": None, "speed": None}, before["observed"]
            )
            self.assertEqual("incomplete", before["provenance"])

            after = result["inference"]["implementer"]
            self.assertEqual(before["requested"], after["requested"])
            # What the settings document holds, not the path it briefly had.
            self.assertEqual(
                {"--effort": "high", "--settings": {"fastMode": True}},
                after["native"],
            )
            # The session named no model, and the requested one is not borrowed.
            self.assertEqual(
                {"model": None, "effort": None, "speed": None}, after["observed"]
            )
            self.assertEqual("incomplete", after["provenance"])

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
            self.assertIn("configuration error", result["decision"]["reasons"][0])

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

    def test_a_red_gate_stops_the_run_before_any_observation(self) -> None:
        stale = "grep -q 'return 0' app.py"
        for phase, constitution_text in (
            ("quick", constitution(quick_command=stale)),
            ("full", constitution(full_command=stale)),
        ):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temp:
                case = build_case(
                    Path(temp),
                    implementer="implement(ACCEPTABLE)",
                    constitution_text=constitution_text,
                )

                result = case.run()

                self.assertEqual("REJECTED", result["status"])
                self.assertNotIn("review", result)
                evidence = json.loads(
                    Path(result["run_dir"], "evidence.json").read_text(encoding="utf-8")
                )
                self.assertNotIn("review", evidence)
                self.assertFalse(Path(result["run_dir"], "review").exists())

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
            self.assertEqual(9, evidence["schema_version"])
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
            gate_isolation = evidence["gate_isolation"]
            self.assertEqual("macos-sandbox-exec", gate_isolation["mechanism"])
            self.assertEqual([], gate_isolation["denied_paths"])
            self.assertEqual(["."], gate_isolation["read_only_paths"])
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

    def _assert_inference(self, actuator: str, evidence: dict) -> None:
        """The profile the run requested, sent and observed, for one backend."""
        implementer = evidence["inference"]["implementer"]

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
            self.assertEqual("test-model", implementer["observed"]["model"])
            self.assertEqual("complete", implementer["provenance"])
        else:
            self.assertEqual(
                {"model_reasoning_effort": "high", "service_tier": "priority"},
                implementer["native"],
            )
            self.assertIsNone(implementer["observed"]["model"])
            self.assertEqual("incomplete", implementer["provenance"])
        # Neither field survives from an earlier iteration of the same run.
        last = evidence["iterations"][-1]["agent"]
        self.assertEqual(2, len(evidence["iterations"]))
        self.assertEqual(last["native"], implementer["native"])
        self.assertEqual(last["observed"], implementer["observed"])
        self.assertIsNone(implementer["observed"]["effort"])
        self.assertIsNone(implementer["observed"]["speed"])

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
