"""Scripted backends and probe scripts the end-to-end cases are driven with.

Each fake CLI answers the flags the real one answers and writes what the
adapter reads back, so a case exercises the controller and never the
network.
"""

import base64
import json

from harness import SENSOR_COMMAND

# What the two fake backends bill, in each backend's own spelling: the Codex
# input count is a total holding the cached part, the Claude counts stand apart.
CODEX_USAGE = {
    "input_tokens": 1200,
    "cached_input_tokens": 1000,
    "cache_write_input_tokens": 0,
    "output_tokens": 300,
    "reasoning_output_tokens": 50,
}
CLAUDE_USAGE = {
    "inputTokens": 200,
    "cacheReadInputTokens": 1000,
    "cacheCreationInputTokens": 0,
    "outputTokens": 300,
    "thinkingTokens": 50,
    "costUSD": 0.0,
}

JOURNAL_PROBE = '''"""Report the transitions the journal already holds."""
import json
import pathlib
import sys

tree = pathlib.Path.cwd()
journal = tree.parents[2] / "runs" / tree.parent.name / tree.name / "events.jsonl"
recorded = [
    json.loads(line)["type"]
    for line in journal.read_text(encoding="utf-8").splitlines()
]
required = [
    "run.started",
    "inputs.frozen",
    "baseline.finished",
    "workspace.ready",
    "actuator.finished",
]
absent = [name for name in required if name not in recorded]
if absent:
    sys.stderr.write("the journal is missing " + " ".join(absent))
    raise SystemExit(1)
print(" ".join(recorded))
'''


CONVERGING_IMPLEMENTER = """
app = worktree / "app.py"
implement(ACCEPTABLE if "return 1" in app.read_text() else UNACCEPTABLE)
"""


LOCATING_REVIEWER = """
emit_review(
    {
        "criteria": SATISFIED["criteria"],
        "findings": [
            {
                "severity": "minor",
                "path": str(worktree / "app.py"),
                "line": 1,
                "message": "a note about the candidate",
                "evidence": "app.py",
            }
        ],
    }
)
"""


MUTATING_SENSOR = f"{SENSOR_COMMAND} && echo mutated > mutant.py"


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


FAKE_CODEX = f"""#!/usr/bin/env python3
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
    print(json.dumps({{"argv": args}}))
    print(json.dumps({{"type": "turn.completed", "usage": {CODEX_USAGE}}}))
    out.write_text(json.dumps(REVIEW))
else:
    probe_implementer_isolation(worktree)
    next_implementation(worktree)
    out.write_text("implemented")
    print(json.dumps({{"type": "message", "message": "done"}}))
    print(json.dumps({{"type": "turn.completed", "usage": {CODEX_USAGE}}}))
"""


FAKE_CLAUDE = f"""#!/usr/bin/env python3
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
            # One object and no init event: the model the reviewer ran on is
            # named only by the record of what the session billed.
            "usage": {{"cache_creation": {{}}}},
            "modelUsage": {{"test-review-model": {CLAUDE_USAGE}}},
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
                "usage": {{"cache_creation": {{}}}},
                "modelUsage": {{"test-model": {CLAUDE_USAGE}}},
            }}
        )
    )
"""


FAKE_AGENTS = {
    "codex": ("codex", FAKE_CODEX, "codex-cli 0.0-test"),
    "claude": ("claude", FAKE_CLAUDE, "0.0-test (Claude Code)"),
}


OBSERVATION = {
    "schema_version": 1,
    "sensor": "task-outcome",
    "status": "passed",
    "summary": "the candidate returns 2",
    "findings": [],
    "metrics": {"checked": 1, "surviving": 0},
}


def toml_basic(command: str) -> str:
    """One shell command as the TOML basic string of a gate may carry it."""
    return command.replace("\\", "\\\\").replace('"', '\\"')


def writes_observation(document: dict, *, exit_code: int = 0) -> str:
    """A gate command writing one document where the controller told it to.

    The constitution carries this as a TOML literal string, so it holds no
    single quote; the shell escapes the double quotes of the JSON itself.
    """
    escaped = json.dumps(document, sort_keys=True).replace('"', '\\"')
    return f'printf %s "{escaped}" > "$CODESERVO_OBSERVATION_PATH"; exit {exit_code}'


def junit_report(
    *, suite: str = "suite", passed: int = 2, failed: int = 0, errors: int = 0
) -> str:
    """One JUnit XML report, single-quoted throughout so a shell can carry it."""
    cases = [
        f"<testcase classname='{suite}' name='ok{n}' time='0.01'/>"
        for n in range(passed)
    ]
    cases += [
        f"<testcase classname='{suite}' name='bad{n}' time='0.02'>"
        f"<failure message='expected 2 but was {n}' type='AssertionError'>trace</failure>"
        "</testcase>"
        for n in range(failed)
    ]
    cases += [
        f"<testcase classname='{suite}' name='broken{n}'>"
        "<error message='boom' type='RuntimeError'>trace</error></testcase>"
        for n in range(errors)
    ]
    total = passed + failed + errors
    return (
        f"<testsuite name='{suite}' tests='{total}' failures='{failed}'"
        f" errors='{errors}' skipped='0' time='0.5'>{''.join(cases)}</testsuite>"
    )


def writes_junit_report(
    report: str, *, into: str = "reports/TEST-suite.xml", exit_code: int = 0
) -> str:
    """A gate command writing one report where its tool would, in the tree.

    The constitution carries this in a double-quoted TOML string, so every
    double quote is escaped for TOML; the report itself holds only single
    quotes, which the shell's double quotes carry unchanged.
    """
    directory = into.rsplit("/", 1)[0] if "/" in into else "."
    return (
        f"mkdir -p {directory} && printf %s "
        + '\\"'
        + report
        + '\\"'
        + f" > {into}; exit {exit_code}"
    )


def sarif_report(
    *, errors: int = 1, warnings: int = 0, tool: str = "ruff", path: str = "app.py"
) -> str:
    """One SARIF log of the shape ruff writes, with the results a test wants."""
    results = [
        {
            "ruleId": f"E{index}",
            "level": "error",
            "message": {"text": f"error {index} in {path}"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": path},
                        "region": {"startLine": index + 1},
                    }
                }
            ],
        }
        for index in range(errors)
    ]
    results += [
        {
            "ruleId": f"W{index}",
            "level": "warning",
            "message": {"text": f"warning {index}"},
        }
        for index in range(warnings)
    ]
    return json.dumps(
        {
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {"driver": {"name": tool, "version": "0.12.12"}},
                    "results": results,
                }
            ],
        }
    )


def writes_report(
    report: str, *, into: str = "reports/lint.sarif", exit_code: int = 0
) -> str:
    """A gate command writing one report where its tool would, in the tree.

    The text goes through base64, whose alphabet a TOML string and a shell
    word both carry unchanged: a JSON document quoted for both at once is a
    fixture nobody can read.
    """
    encoded = base64.b64encode(report.encode("utf-8")).decode("ascii")
    directory = into.rsplit("/", 1)[0] if "/" in into else "."
    return (
        f"mkdir -p {directory} && printf %s {encoded} | base64 -d > {into};"
        f" exit {exit_code}"
    )


def lcov_report(*, covered: int = 2, missing: int = 0, path: str = "app.py") -> str:
    """One LCOV tracefile of the shape coverage.py writes."""
    lines = [f"DA:{index + 1},1" for index in range(covered)]
    lines += [f"DA:{covered + index + 1},0" for index in range(missing)]
    return "\n".join([f"SF:{path}", *lines, "end_of_record"]) + "\n"
