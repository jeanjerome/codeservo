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


@unittest.skipIf(os.name == "nt", "fake Codex executable uses a POSIX shebang")
class ControllerE2ETests(unittest.TestCase):
    def test_feedback_loop_converges_and_accepts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            home = root / "home"
            bin_dir = root / "bin"
            repo.mkdir()
            home.mkdir()
            bin_dir.mkdir()
            (repo / ".codeservo").mkdir()

            (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
            (repo / "app.py").write_text("def value():\n    return 0\n", encoding="utf-8")
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
command = "grep -q 'return 2' app.py"
baseline = false

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
import pathlib
import sys
args = sys.argv[1:]
def value(flag):
    return args[args.index(flag) + 1]
worktree = pathlib.Path(value("--cd"))
out = pathlib.Path(value("--output-last-message"))
out.parent.mkdir(parents=True, exist_ok=True)
sys.stdin.read()
if value("--sandbox") == "workspace-write":
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
            subprocess.run(["git", "commit", "-qm", "initial"], cwd=repo, check=True)

            env = {
                "HOME": str(home),
                "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
            }
            with patch.dict(os.environ, env, clear=False):
                result = run(repo_path=repo, task_path=task, max_iterations=3)

            self.assertEqual("ACCEPTED", result["status"])
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
            evidence = json.loads(Path(result["run_dir"], "evidence.json").read_text())
            self.assertEqual(2, evidence["schema_version"])
            self.assertEqual("ACCEPTED", evidence["status"])


if __name__ == "__main__":
    unittest.main()
