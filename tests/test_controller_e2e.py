import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeservo.controller import run


@unittest.skipUnless(
    sys.platform == "darwin",
    "external sensor isolation requires macOS sandbox-exec",
)
class ControllerE2ETests(unittest.TestCase):
    def test_feedback_loop_converges_and_accepts(self) -> None:
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
                f'''version = 1

[scope]
protected = [".codeservo/**"]
max_changed_files = 5
max_diff_lines = 100

[[gate]]
name = "syntax"
phase = "quick"
command = "{sys.executable} -m py_compile app.py"
baseline = true

[[gate]]
name = "task-outcome"
phase = "quick"
command = 'test -f "$CODESERVO_SENSOR_PATH/README.md" && grep -q "return 2" app.py'
baseline = false
sensor = "test/task-outcome"

[[gate]]
name = "full"
phase = "full"
command = "{sys.executable} -m py_compile app.py"
baseline = true

[review]
blocking_severities = ["blocker", "major"]
''',
                encoding="utf-8",
            )
            task = root / "TASK.md"
            task.write_text(
                "# Task\n\n## Acceptance criteria\n- [AC1] `value()` returns `2`.\n",
                encoding="utf-8",
            )

            fake_codex = bin_dir / "codex"
            fake_codex.write_text(
                '''#!/usr/bin/env python3
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
worktree = pathlib.Path(value("--cd"))
out = pathlib.Path(value("--output-last-message"))
out.parent.mkdir(parents=True, exist_ok=True)
sys.stdin.read()
if value("--sandbox") == "workspace-write":
    source_history = subprocess.run(
        [
            "git",
            f"--git-dir={os.environ['CODESERVO_TEST_SOURCE_GIT']}",
            "show",
            "HEAD^:historical-sensor.txt",
        ],
        capture_output=True,
        check=False,
    )
    if source_history.returncode == 0:
        sys.stderr.write("source repository history is readable")
        raise SystemExit(8)
    history = subprocess.run(
        ["git", "show", "HEAD^:historical-sensor.txt"],
        cwd=worktree,
        capture_output=True,
        check=False,
    )
    if history.returncode == 0:
        sys.stderr.write("historical sensor is readable")
        raise SystemExit(9)
    p = worktree / "app.py"
    current = p.read_text()
    p.write_text(
        "def value():\\n    return 1\\n"
        if "return 0" in current
        else "def value():\\n    return 2\\n"
    )
    out.write_text("implemented")
    print(json.dumps({"type": "message", "message": "done"}))
else:
    out.write_text(json.dumps({
        "criteria": [{"id": "AC1", "status": "satisfied", "evidence": "app.py returns 2"}],
        "findings": []
    }))
''',
                encoding="utf-8",
            )
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "historical sensor"],
                cwd=repo,
                check=True,
            )
            historical_blob = subprocess.run(
                ["git", "rev-parse", "HEAD:historical-sensor.txt"],
                cwd=repo,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            historical_sensor.unlink()
            subprocess.run(["git", "add", "-u"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "clean baseline"],
                cwd=repo,
                check=True,
            )

            env = {
                "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
                "CODESERVO_TEST_SOURCE_GIT": str((repo / ".git").resolve()),
            }
            with patch.dict(os.environ, env, clear=False):
                result = run(
                    repo_path=repo,
                    task_path=task,
                    max_iterations=3,
                    state_dir=state_dir,
                )

            self.assertEqual("ACCEPTED", result["status"])
            self.assertEqual(str(state_dir.resolve()), result["state_dir"])
            self.assertTrue(Path(result["run_dir"]).is_relative_to(state_dir.resolve()))
            self.assertTrue(Path(result["worktree"]).is_relative_to(state_dir.resolve()))
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
            self.assertEqual(4, evidence["schema_version"])
            self.assertEqual(".", evidence["run_dir"])
            self.assertFalse(Path(evidence["state_dir"]).is_absolute())
            self.assertFalse(Path(evidence["worktree"]).is_absolute())
            self.assertEqual("codex-cli 0.0-test", evidence["runtime"]["codex_version"])
            frozen_sensor = evidence["sensors"]["task-outcome"]
            frozen_sensor_path = Path(result["run_dir"], frozen_sensor["path"])
            self.assertTrue(frozen_sensor_path.is_dir())
            self.assertTrue(Path(frozen_sensor_path, "README.md").is_file())
            self.assertEqual(
                "macos-sandbox-exec",
                evidence["actuator_isolation"]["mechanism"],
            )
            self.assertIn(
                "../../../sensors",
                evidence["actuator_isolation"]["denied_paths"],
            )
            self.assertTrue(
                all(
                    not Path(path).is_absolute()
                    for path in evidence["actuator_isolation"]["denied_paths"]
                )
            )
            for gate in evidence["baseline"] + evidence["full_gates"]:
                self.assertEqual(64, len(gate["stdout_sha256"]))
                self.assertEqual(64, len(gate["stderr_sha256"]))
                self.assertEqual(64, len(gate["result_sha256"]))
            for iteration in evidence["iterations"]:
                self.assertEqual(64, len(iteration["agent"]["events_sha256"]))
                self.assertEqual(64, len(iteration["agent"]["result_sha256"]))
            self.assertEqual("ACCEPTED", evidence["status"])


if __name__ == "__main__":
    unittest.main()
