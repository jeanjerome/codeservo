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
# What the scripted backend reports about itself. Neither session model is one
# a case ever requests, so a record repeating the request is visible as such.
# The implementer bills its tokens under a model the catalogue prices, so a
# run carries a computed cost; the reviewer bills under one it does not.
AGENT_MODEL = "harness-agent-model"
REVIEW_MODEL = "harness-review-model"
BILLED_MODEL = "claude-haiku-4-5-20251001"
# What every case requests unless it says otherwise: a catalogue model of the
# backend the scripted `claude` stands in for, at the lightest effort.
REQUESTED_MODEL = "claude-haiku-4-5-20251001"
REQUESTED_EFFORT = "low"
# The tokens the scripted implementer bills per actuation. At the catalogue's
# prices for the billed model they cost 0.0097 USD: 1000 input at 1, 2000 cache
# reads at 0.1, 500 one-hour writes at 2, 1500 output at 5, per million.
AGENT_TOKENS = {
    "inputTokens": 1000,
    "cacheReadInputTokens": 2000,
    "cacheCreationInputTokens": 500,
    "outputTokens": 1500,
    "thinkingTokens": 100,
}
AGENT_COST_USD = 0.0097
SENSOR_COMMAND = (
    'test -f "$CODESERVO_SENSOR_PATH/README.md" && grep -q "return 2" app.py'
)
COMPILE_COMMAND = f"{sys.executable} -m py_compile app.py"

_AGENT_TEMPLATE = """#!/usr/bin/env python3
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
    # The stream names the model its alias resolved to, which is not the one
    # the command line asked for.
    print(
        json.dumps(
            {{"type": "system", "subtype": "init", "model": "{agent_model}"}}
        )
    )
    print(
        json.dumps(
            {{
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "num_turns": 1,
                "session_id": "agent",
                "result": message,
                "usage": {{
                    "cache_creation": {{
                        "ephemeral_1h_input_tokens": 500,
                        "ephemeral_5m_input_tokens": 0
                    }}
                }},
                "modelUsage": {{
                    "{billed_model}": {{
                        "inputTokens": 1000,
                        "cacheReadInputTokens": 2000,
                        "cacheCreationInputTokens": 500,
                        "outputTokens": 1500,
                        "thinkingTokens": 100,
                        "costUSD": 0.0097
                    }}
                }},
            }}
        )
    )


def emit_review(payload):
    # One object and no init event: the reviewer's model is only in the record
    # of what the session billed.
    json.dump(
        {{
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": 1,
            "session_id": "review",
            "result": json.dumps(payload),
            "structured_output": payload,
            "usage": {{"cache_creation": {{}}}},
            "modelUsage": {{
                "{review_model}": {{"inputTokens": 40, "outputTokens": 4, "costUSD": 0.0}}
            }},
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
"""


def agent_script(*, implementer: str, reviewer: str = "emit_review(SATISFIED)") -> str:
    return _AGENT_TEMPLATE.format(
        implementer=textwrap.indent(textwrap.dedent(implementer).strip(), "    "),
        reviewer=textwrap.indent(textwrap.dedent(reviewer).strip(), "    "),
        agent_model=AGENT_MODEL,
        review_model=REVIEW_MODEL,
        billed_model=BILLED_MODEL,
    )


def constitution(
    *,
    quick_command: str = COMPILE_COMMAND,
    full_command: str = COMPILE_COMMAND,
    sensor_command: str | None = SENSOR_COMMAND,
    max_changed_files: int = 5,
    execution: str | None = None,
    quick_task: str | None = None,
    sensor_phase: str = "quick",
    quick_result_format: str | None = None,
    sensor_result_format: str | None = None,
    quick_ratchet: str | None = None,
    full_result_format: str | None = None,
    full_ratchet: str | None = None,
    provider_name: str = "pixi",
    sensor_task: str | None = None,
) -> str:
    """The constitution a case runs under.

    `sensor_phase` places the external sensor, the one gate a run measures the
    candidate with and never the source repository. The two `result_format`
    arguments declare what a gate answers with beside its exit code: the quick
    gate is a baseline one, the external sensor never is.
    """
    text = f"""version = 1

[scope]
protected = [".codeservo/**"]
max_changed_files = {max_changed_files}
max_diff_lines = 100
"""
    if execution is not None:
        manifest = "pyproject.toml" if provider_name == "pixi" else "mise.toml"
        text += f"""
[execution]
provider = "{provider_name}"
manifest = "{manifest}"
environment = "{execution}"
"""
    quick = (
        f'task = "{quick_task}"'
        if quick_task is not None
        else f'command = "{quick_command}"'
    )
    if quick_result_format is not None:
        quick += f'\nresult_format = "{quick_result_format}"'
    if quick_ratchet is not None:
        quick += f"\nratchet = {quick_ratchet}"
    full = f'command = "{full_command}"'
    if full_result_format is not None:
        full += f'\nresult_format = "{full_result_format}"'
    if full_ratchet is not None:
        full += f"\nratchet = {full_ratchet}"
    text += f"""
[[gate]]
name = "syntax"
phase = "quick"
{quick}
baseline = true

[[gate]]
name = "full"
phase = "full"
{full}
baseline = true
"""
    if sensor_task is not None:
        text += f"""
[[gate]]
name = "task-outcome"
phase = "{sensor_phase}"
task = "{sensor_task}"
baseline = false
sensor = "test/task-outcome"
"""
    elif sensor_command is not None:
        text += f"""
[[gate]]
name = "task-outcome"
phase = "{sensor_phase}"
command = '{sensor_command}'
baseline = false
sensor = "test/task-outcome"
"""
        if sensor_result_format is not None:
            text += f'result_format = "{sensor_result_format}"\n'
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
            "model": REQUESTED_MODEL,
            "effort": REQUESTED_EFFORT,
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
    source_environment: bool = True,
    provider_name: str = "pixi",
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
    if provider and provider_name == "mise":
        write_mise_provider(bin_dir, repo, stale_lock=stale_lock)
    elif provider:
        write_provider(
            bin_dir, repo, stale_lock=stale_lock, installed=source_environment
        )
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
        # A fixture repository carries no signature: signing would make the
        # suite depend on the operator's configuration and on a signing tool
        # nothing declares.
        subprocess.run(
            ["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True
        )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True)


# --- Execution provider fixtures ------------------------------------------
#
# A stand-in for pixi. It answers the four commands the controller runs, and
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

_PIXI_SCRIPT = (
    '''"""A stand-in for pixi, answering from the manifest it names."""
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

PACKAGES = '''
    + repr(PIXI_PACKAGES)
    + """
OPERATOR = """
    + repr(PIXI_OPERATOR_PATHS)
    + """

args = sys.argv[1:]
subcommand = args[0] if args else ""

log = os.environ.get("CODESERVO_TEST_PIXI_LOG")
if log:
    seen = {
        name: os.environ[name]
        for name in ("PIXI_OFFLINE", "PIXI_NO_INSTALL", "PIXI_FROZEN")
        if name in os.environ
    }
    with open(log, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"args": args, "env": seen}) + "\\n")


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


def prefix_of(environment):
    return manifest.parent / ".pixi" / "envs" / environment


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
            {
                "name": name,
                "tasks": sorted(tasks_of(name)),
                "prefix": str(prefix_of(name)),
            }
            for name in environments()
        ],
    }
    description.update(OPERATOR)
    json.dump(description, sys.stdout)
    raise SystemExit(0)

if subcommand == "install":
    require("--locked", "--no-config", "--environment", "--manifest-path")
    environment = flag("--environment")
    if environment not in environments():
        sys.stderr.write("unknown environment " + environment + "\\n")
        raise SystemExit(1)
    if os.environ.get("CODESERVO_TEST_PIXI_INSTALL_FAILS"):
        sys.stderr.write("Error:   x failed to install " + environment + "\\n")
        raise SystemExit(1)
    lock = manifest.parent / "pixi.lock"
    if "stale" in lock.read_text(encoding="utf-8"):
        # It refuses without rewriting what it refuses, and creates nothing.
        sys.stderr.write("Error:   x lock file not up-to-date with the workspace\\n")
        raise SystemExit(1)
    if os.environ.get("PIXI_NO_INSTALL") or os.environ.get("PIXI_FROZEN"):
        # A no-op that still reports success.
        raise SystemExit(0)
    prefix_of(environment).mkdir(parents=True, exist_ok=True)
    # The environment directory ignores itself, whatever the repository says.
    (manifest.parent / ".pixi" / ".gitignore").write_text(
        "*\\n!config.toml\\n", encoding="utf-8"
    )
    print("installed " + environment)
    raise SystemExit(0)

if subcommand == "run":
    require("--as-is", "--clean-env", "--no-config")
    environment = flag("--environment")
    if environment not in environments():
        sys.stderr.write("unknown environment " + environment + "\\n")
        raise SystemExit(1)
    if not prefix_of(environment).is_dir():
        # `--clean-env` on a missing environment fails loudly instead of
        # silently measuring the operator's ambient interpreter.
        sys.stderr.write("python: command not found\\n")
        raise SystemExit(127)
    task = args[-1]
    # An unknown task is not an error: the name is executed as a program.
    command = tasks_of(environment).get(task, task)
    print("pixi run " + task + " in " + str(manifest))
    sys.stdout.flush()
    raise SystemExit(subprocess.run(command, shell=True).returncode)

sys.stderr.write("unexpected subcommand " + subcommand + "\\n")
raise SystemExit(95)
"""
)


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


def write_provider(
    bin_dir: Path,
    repo: Path,
    *,
    stale_lock: bool = False,
    installed: bool = True,
) -> None:
    """Install the stand-in provider and the workspace it answers about.

    `installed` is the operator having prepared the source repository: the
    controller never installs there, so a baseline task gate finds the
    environment or the run refuses to measure through it.
    """
    (repo / "pyproject.toml").write_text(pixi_manifest(), encoding="utf-8")
    (repo / "pixi.lock").write_text(pixi_lock(stale=stale_lock), encoding="utf-8")
    if installed:
        (repo / ".pixi" / "envs" / "default").mkdir(parents=True, exist_ok=True)
        (repo / ".pixi" / "envs" / "gates").mkdir(parents=True, exist_ok=True)
        (repo / ".pixi" / ".gitignore").write_text(
            "*\n!config.toml\n", encoding="utf-8"
        )
    provider = bin_dir / "pixi"
    provider.write_text(f"#!{sys.executable}\n" + _PIXI_SCRIPT, encoding="utf-8")
    provider.chmod(provider.stat().st_mode | stat.S_IXUSR)


# --- A stand-in for mise -------------------------------------------------------
#
# It answers the five commands the controller runs from the manifest beside the
# directory it is given, keeps its tools under the data directory it is handed,
# refuses to install anything the lockfile does not pin, and refuses the one
# command that rewrites the lockfile.

MISE_TASK = "check-syntax"
MISE_SENSOR_TASK = "sensor-check"
MISE_TOOL = "fake-tool"
MISE_TOOL_VERSION = "1.2.3"
MISE_VARIABLES = (
    "MISE_OFFLINE",
    "MISE_LOCKED",
    "MISE_YES",
    "MISE_DATA_DIR",
    "MISE_AUTO_INSTALL",
    "MISE_EXEC_AUTO_INSTALL",
    "MISE_NOT_FOUND_AUTO_INSTALL",
    "MISE_TASK_RUN_AUTO_INSTALL",
    "MISE_TRUSTED_CONFIG_PATHS",
    "MISE_OVERRIDE_CONFIG_FILENAMES",
    "MISE_CEILING_PATHS",
    "MISE_GLOBAL_CONFIG_FILE",
    "MISE_SYSTEM_CONFIG_FILE",
)

_MISE_SCRIPT = (
    '''"""A stand-in for mise, answering from the manifest in the directory it is given."""
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

VARIABLES = '''
    + repr(MISE_VARIABLES)
    + """

args = sys.argv[1:]
subcommand = args[0] if args else ""

log = os.environ.get("CODESERVO_TEST_MISE_LOG")
if log:
    seen = {name: os.environ[name] for name in VARIABLES if name in os.environ}
    with open(log, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"args": args, "env": seen}) + "\\n")


def flag(name, default=""):
    return args[args.index(name) + 1] if name in args else default


def require(*options):
    missing = [option for option in options if option not in args]
    if missing:
        sys.stderr.write("missing options: " + " ".join(missing) + "\\n")
        raise SystemExit(96)


directory = Path(flag("-C", os.getcwd()))
manifest = directory / os.environ.get("MISE_OVERRIDE_CONFIG_FILENAMES", "mise.toml")
data_dir = Path(os.environ["MISE_DATA_DIR"]) if "MISE_DATA_DIR" in os.environ else None


def config():
    return tomllib.loads(manifest.read_text(encoding="utf-8"))


def locked():
    lock = manifest.with_name("mise.lock")
    return tomllib.loads(lock.read_text(encoding="utf-8")).get("tools", {})


def locked_version(tool, specifier):
    for entry in locked().get(tool, []):
        if specifier in entry.get("specifiers", []):
            return entry["version"]
    return None


def install_path(tool, version):
    return data_dir / "installs" / tool / version


def every_tool():
    for tool, specifier in config().get("tools", {}).items():
        yield tool, specifier, locked_version(tool, specifier)


if subcommand == "lock":
    sys.stderr.write("mise lock rewrites the lockfile and must never run\\n")
    raise SystemExit(97)

if data_dir is None:
    sys.stderr.write("the stand-in keeps its tools under MISE_DATA_DIR only\\n")
    raise SystemExit(94)

if subcommand == "version":
    require("--json")
    json.dump(
        {"version": "2026.9.1-test test-os-test-arch", "os": "test-os", "arch": "test-arch"},
        sys.stdout,
    )
    raise SystemExit(0)

if subcommand == "tasks":
    require("ls", "--json")
    tasks = config().get("tasks", {})
    json.dump(
        [
            {
                "name": name,
                "run": [spec if isinstance(spec, str) else spec["run"]],
                "source": str(manifest),
            }
            for name, spec in tasks.items()
        ],
        sys.stdout,
    )
    raise SystemExit(0)

if subcommand == "ls":
    require("--json", "--current")
    listed = {}
    for tool, specifier, version in every_tool():
        if version is None:
            sys.stderr.write(
                f"mise WARN  Failed to resolve tool version list for {tool}:"
                f" {tool}@{specifier} is not in the lockfile\\n"
            )
            continue
        listed[tool] = [
            {
                "version": version,
                "requested_version": specifier,
                "install_path": str(install_path(tool, version)),
                "installed": install_path(tool, version).is_dir(),
                "active": True,
            }
        ]
    json.dump(listed, sys.stdout)
    raise SystemExit(0)

if subcommand == "install":
    if os.environ.get("MISE_LOCKED") != "1":
        sys.stderr.write("an install without MISE_LOCKED would resolve\\n")
        raise SystemExit(98)
    if "--dry-run-code" in args:
        missing = [t for t, s, v in every_tool() if v is None or not install_path(t, v).is_dir()]
        raise SystemExit(1 if missing else 0)
    if os.environ.get("CODESERVO_TEST_MISE_INSTALL_FAILS"):
        sys.stderr.write("mise ERROR failed to install\\n")
        raise SystemExit(1)
    for tool, specifier, version in every_tool():
        if version is None:
            sys.stderr.write(f"mise ERROR {tool}@{specifier} is not in the lockfile\\n")
            raise SystemExit(1)
        install_path(tool, version).mkdir(parents=True, exist_ok=True)
    print("mise installed")
    raise SystemExit(0)

if subcommand == "run":
    require("-q", "-C")
    rest = [item for item in args[1:] if item not in ("-q",)]
    rest = rest[rest.index("-C") + 2 :]
    task = rest[0]
    extra = rest[2:] if len(rest) > 1 and rest[1] == "--" else rest[1:]
    for tool, specifier, version in every_tool():
        if version is None or not install_path(tool, version).is_dir():
            # A missing tool fails loudly: auto-installation is off.
            sys.stderr.write(f"mise ERROR {tool} is not installed\\n")
            raise SystemExit(127)
    spec = config().get("tasks", {}).get(task)
    if spec is None:
        sys.stderr.write(f"mise ERROR no task {task}\\n")
        raise SystemExit(1)
    command = spec if isinstance(spec, str) else spec["run"]
    sys.stdout.flush()
    raise SystemExit(
        subprocess.run(
            " ".join([command, *extra]), shell=True, cwd=directory
        ).returncode
    )

sys.stderr.write("unexpected subcommand " + subcommand + "\\n")
raise SystemExit(95)
"""
)


def mise_manifest() -> str:
    """A manifest pinning one tool and declaring the tasks the cases run."""
    return f"""[tools]
{MISE_TOOL} = "{MISE_TOOL_VERSION[:1]}"

[settings]
lockfile = true

[tasks.{MISE_TASK}]
run = "{sys.executable} -m py_compile app.py"

[tasks.{MISE_SENSOR_TASK}]
run = 'test -f "$CODESERVO_SENSOR_PATH/README.md" && grep -q "return 2" app.py'
"""


def mise_lock(*, stale: bool = False) -> str:
    """The lockfile pinning the tool, or one pinning it for another specifier."""
    specifier = "9" if stale else MISE_TOOL_VERSION[:1]
    return f"""lockfile_version = 1

[[tools.{MISE_TOOL}]]
version = "{MISE_TOOL_VERSION}"
backend = "core:{MISE_TOOL}"
specifiers = ["{specifier}"]
"""


def write_mise_provider(bin_dir: Path, repo: Path, *, stale_lock: bool = False) -> None:
    """Install the stand-in mise and the manifest it answers about."""
    (repo / "mise.toml").write_text(mise_manifest(), encoding="utf-8")
    (repo / "mise.lock").write_text(mise_lock(stale=stale_lock), encoding="utf-8")
    executable = bin_dir / "mise"
    executable.write_text(f"#!{sys.executable}\n" + _MISE_SCRIPT, encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
