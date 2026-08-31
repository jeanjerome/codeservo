"""Fixtures driving the control loop with a scripted actuator."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from codeservo.controller import run
from isolation_harness import controller_test_isolation

TASK_TEXT = "# Task\n\n## Acceptance criteria\n- [AC1] `value()` returns `2`.\n"
SENSOR_COMMAND = 'test -f "$CODESERVO_SENSOR_PATH/README.md" && grep -q "return 2" app.py'
COMPILE_COMMAND = f"{sys.executable} -m py_compile app.py"

_AGENT_TEMPLATE = '''#!/usr/bin/env python3
import json
import os
import pathlib
import sys
import time

args = sys.argv[1:]
if "--version" in args:
    print("0.0-test (Claude Code)")
    raise SystemExit(0)


def flag(name):
    return args[args.index(name) + 1]


def emit_agent(message="implemented"):
    print(json.dumps({{"type": "system", "subtype": "init"}}))
    print(
        json.dumps(
            {{
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "num_turns": 1,
                "session_id": "agent",
                "result": message,
            }}
        )
    )


def emit_review(payload):
    json.dump(
        {{
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": 1,
            "session_id": "review",
            "result": json.dumps(payload),
            "structured_output": payload,
        }},
        sys.stdout,
    )


def implement(source):
    (worktree / "app.py").write_text(source)
    emit_agent()


SATISFIED = {{
    "criteria": [
        {{"id": "AC1", "status": "satisfied", "evidence": "app.py returns 2"}}
    ],
    "findings": [],
}}
ACCEPTABLE = "def value():\\n    return 2\\n"
UNACCEPTABLE = "def value():\\n    return 1\\n"

worktree = pathlib.Path.cwd()
sys.stdin.read()
if flag("--output-format") == "json":
{reviewer}
else:
{implementer}
'''


def agent_script(
    *, implementer: str, reviewer: str = "emit_review(SATISFIED)"
) -> str:
    return _AGENT_TEMPLATE.format(
        implementer=textwrap.indent(textwrap.dedent(implementer).strip(), "    "),
        reviewer=textwrap.indent(textwrap.dedent(reviewer).strip(), "    "),
    )


def constitution(
    *,
    quick_command: str = COMPILE_COMMAND,
    full_command: str = COMPILE_COMMAND,
    sensor_command: str | None = SENSOR_COMMAND,
    max_changed_files: int = 5,
) -> str:
    text = f"""version = 1

[scope]
protected = [".codeservo/**"]
max_changed_files = {max_changed_files}
max_diff_lines = 100

[[gate]]
name = "syntax"
phase = "quick"
command = "{quick_command}"
baseline = true

[[gate]]
name = "full"
phase = "full"
command = "{full_command}"
baseline = true
"""
    if sensor_command is not None:
        text += f"""
[[gate]]
name = "task-outcome"
phase = "quick"
command = '{sensor_command}'
baseline = false
sensor = "test/task-outcome"
"""
    text += """
[review]
blocking_severities = ["blocker", "major"]
"""
    return text


@dataclass(frozen=True)
class Case:
    root: Path
    repo: Path
    state_dir: Path
    task: Path
    bin_dir: Path

    def run(self, *, env: dict[str, str] | None = None, **overrides) -> dict:
        arguments = {
            "repo_path": self.repo,
            "task_path": self.task,
            "state_dir": self.state_dir,
            "actuator": "claude",
            "max_iterations": 2,
        }
        arguments.update(overrides)
        path = str(self.bin_dir) + os.pathsep + os.environ.get("PATH", "")
        with controller_test_isolation():
            with patch.dict(os.environ, {"PATH": path, **(env or {})}, clear=False):
                return run(**arguments)


def build_case(
    root: Path,
    *,
    implementer: str,
    reviewer: str = "emit_review(SATISFIED)",
    constitution_text: str | None = None,
) -> Case:
    repo = root / "repo"
    state_dir = root / "state"
    bin_dir = root / "bin"
    (repo / ".codeservo").mkdir(parents=True)
    bin_dir.mkdir()
    sensor = state_dir / "sensors" / "test" / "task-outcome"
    sensor.mkdir(parents=True)
    (sensor / "README.md").write_text(
        "Controller-owned test sensor.\n", encoding="utf-8"
    )

    (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    (repo / "app.py").write_text("def value():\n    return 0\n", encoding="utf-8")
    (repo / ".codeservo" / "constitution.toml").write_text(
        constitution_text if constitution_text is not None else constitution(),
        encoding="utf-8",
    )
    task = root / "TASK.md"
    task.write_text(TASK_TEXT, encoding="utf-8")

    agent = bin_dir / "claude"
    agent.write_text(
        agent_script(implementer=implementer, reviewer=reviewer), encoding="utf-8"
    )
    agent.chmod(agent.stat().st_mode | stat.S_IXUSR)

    commit_repository(repo)
    return Case(root=root, repo=repo, state_dir=state_dir, task=task, bin_dir=bin_dir)


def commit_repository(repo: Path, message: str = "baseline") -> None:
    if not (repo / ".git").is_dir():
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True)
