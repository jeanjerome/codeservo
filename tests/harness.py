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
    execution: str | None = None,
    quick_task: str | None = None,
) -> str:
    text = f"""version = 1

[scope]
protected = [".codeservo/**"]
max_changed_files = {max_changed_files}
max_diff_lines = 100
"""
    if execution is not None:
        text += f"""
[execution]
provider = "pixi"
manifest = "pyproject.toml"
environment = "{execution}"
"""
    quick = (
        f'task = "{quick_task}"'
        if quick_task is not None
        else f'command = "{quick_command}"'
    )
    text += f"""
[[gate]]
name = "syntax"
phase = "quick"
{quick}
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
    provider: bool = False,
    stale_lock: bool = False,
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
    if provider:
        write_provider(bin_dir, repo, stale_lock=stale_lock)
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


# --- Execution provider fixtures ------------------------------------------
#
# A stand-in for pixi. It answers the three commands the controller runs, and
# refuses the one it must never run, so a run can be driven end to end without
# an installed provider or a solved environment.

PIXI_TASK = "check-syntax"
PIXI_PACKAGES = [
    {"name": "python", "version": "3.12.0", "kind": "conda"},
    {"name": "hatchling", "version": "1.25.0", "kind": "conda"},
]
# Locations belonging to the operator, never to a run.
PIXI_OPERATOR_PATHS = {
    "cache_dir": "/operator/cache",
    "auth_dir": "/operator/credentials",
    "config_locations": ["/operator/config.toml"],
    "global_info": {"bin_dir": "/operator/bin"},
}

_PIXI_SCRIPT = '''"""A stand-in for pixi, answering from the manifest it names."""
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

PACKAGES = ''' + repr(PIXI_PACKAGES) + '''
OPERATOR = ''' + repr(PIXI_OPERATOR_PATHS) + '''

args = sys.argv[1:]
subcommand = args[0] if args else ""

log = os.environ.get("CODESERVO_TEST_PIXI_LOG")
if log:
    with open(log, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(args) + "\\n")


def flag(name, default=""):
    return args[args.index(name) + 1] if name in args else default


def require(*options):
    missing = [option for option in options if option not in args]
    if missing:
        sys.stderr.write("missing options: " + " ".join(missing) + "\\n")
        raise SystemExit(96)


manifest = Path(flag("--manifest-path"))


def workspace():
    return tomllib.loads(manifest.read_text(encoding="utf-8")).get("tool", {}).get(
        "pixi", {}
    )


def tasks_of(environment):
    pixi = workspace()
    tasks = dict(pixi.get("tasks", {}))
    for feature in pixi.get("environments", {}).get(environment, []):
        tasks.update(pixi.get("feature", {}).get(feature, {}).get("tasks", {}))
    return tasks


def environments():
    return ["default", *workspace().get("environments", {})]


if subcommand == "lock":
    sys.stderr.write("pixi lock rewrites the lockfile and must never run\\n")
    raise SystemExit(97)

if subcommand == "list":
    require("--json", "--locked", "--no-install", "--no-config")
    lock = manifest.parent / "pixi.lock"
    if "stale" in lock.read_text(encoding="utf-8"):
        sys.stderr.write("Error:   x lock file not up-to-date with the workspace\\n")
        raise SystemExit(1)
    json.dump(PACKAGES, sys.stdout)
    raise SystemExit(0)

if subcommand == "info":
    require("--json", "--no-config")
    description = {
        "version": "0.77.1-test",
        "platform": "test-platform",
        "environments_info": [
            {"name": name, "tasks": sorted(tasks_of(name))}
            for name in environments()
        ],
    }
    description.update(OPERATOR)
    json.dump(description, sys.stdout)
    raise SystemExit(0)

if subcommand == "run":
    require("--as-is", "--clean-env", "--no-config")
    environment = flag("--environment")
    if environment not in environments():
        sys.stderr.write("unknown environment " + environment + "\\n")
        raise SystemExit(1)
    task = args[-1]
    # An unknown task is not an error: the name is executed as a program.
    command = tasks_of(environment).get(task, task)
    print("pixi run " + task + " in " + str(manifest))
    sys.stdout.flush()
    raise SystemExit(subprocess.run(command, shell=True).returncode)

sys.stderr.write("unexpected subcommand " + subcommand + "\\n")
raise SystemExit(95)
'''


def pixi_manifest() -> str:
    """A workspace declaring one task in the default environment."""
    return f"""[tool.pixi.workspace]
platforms = ["test-platform"]

[tool.pixi.tasks]
{PIXI_TASK} = "{sys.executable} -m py_compile app.py"

[tool.pixi.feature.extra.tasks]
extra-check = "{sys.executable} -c 'pass'"

[tool.pixi.environments]
gates = ["extra"]
"""


def pixi_lock(*, stale: bool = False) -> str:
    return "version: 6\n" + ("environments: stale\n" if stale else "environments: {}\n")


def write_provider(bin_dir: Path, repo: Path, *, stale_lock: bool = False) -> None:
    """Install the stand-in provider and the workspace it answers about."""
    (repo / "pyproject.toml").write_text(pixi_manifest(), encoding="utf-8")
    (repo / "pixi.lock").write_text(pixi_lock(stale=stale_lock), encoding="utf-8")
    provider = bin_dir / "pixi"
    provider.write_text(f"#!{sys.executable}\n" + _PIXI_SCRIPT, encoding="utf-8")
    provider.chmod(provider.stat().st_mode | stat.S_IXUSR)
