import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness import TASK_TEXT, Case, commit_repository, constitution

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


def should_probe_isolation():
    return os.environ.get("CODESERVO_TEST_NESTED_SEATBELT") != "1"
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
    if should_probe_isolation():
        probe_read_only(worktree)
    out.write_text(json.dumps(REVIEW))
else:
    if should_probe_isolation():
        probe_isolation(worktree)
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
    if should_probe_isolation():
        probe_read_only(worktree)
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
    if should_probe_isolation():
        probe_isolation(worktree)
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
                },
                actuator=actuator,
                max_iterations=3,
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
            self.assertEqual(7, evidence["schema_version"])
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
            self.assertEqual("ACCEPTED", evidence["status"])


if __name__ == "__main__":
    unittest.main()
