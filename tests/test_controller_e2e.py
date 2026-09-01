import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeservo import claude_code, codex
from codeservo.events import JOURNAL_NAME, read_journal
from codeservo.evidence import sha256_file, sha256_json, sha256_text
from codeservo.verify import verify_run
from harness import (
    COMPILE_COMMAND,
    PIXI_PACKAGES,
    PIXI_TASK,
    SENSOR_COMMAND,
    TASK_TEXT,
    Case,
    build_case,
    commit_repository,
    constitution,
)

# A gate that reads the run journal from the tree it measures. The two
# locations a run owns are siblings, so a gate measuring the candidate names
# the record without being told where it is.
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

# Two turns: the first candidate fails the external sensor, the second passes.
CONVERGING_IMPLEMENTER = '''
app = worktree / "app.py"
implement(ACCEPTABLE if "return 1" in app.read_text() else UNACCEPTABLE)
'''

# A reviewer naming a location of the candidate it read, as an absolute path.
LOCATING_REVIEWER = '''
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
'''

# A gate that measures, exits zero, and leaves a file behind.
MUTATING_SENSOR = f"{SENSOR_COMMAND} && echo mutated > mutant.py"


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
    print(json.dumps({{"argv": args}}))
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
            # Both roles exist in the persisted record before anything starts.
            self.assertEqual({"implementer", "reviewer"}, set(frozen[0]["inference"]))
            self.assertEqual(
                {
                    "backend": "claude",
                    "model": None,
                    "effort": None,
                    "speed": "standard",
                },
                frozen[0]["inference"]["reviewer"]["requested"],
            )
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

    def _reviewer_argv(self, run_dir: str) -> list[str]:
        """The command line the fake Codex reviewer reported it was given."""
        stdout = Path(run_dir, "review", "stdout.log").read_text(encoding="utf-8")
        for line in stdout.splitlines():
            payload = json.loads(line)
            if "argv" in payload:
                return ["codex", *payload["argv"]]
        raise AssertionError(f"the reviewer reported no command line: {stdout}")

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
            self.assertEqual(
                1, len(result["decision"]["reasons"]), result["decision"]["reasons"]
            )
            self.assertIn("configuration error", result["decision"]["reasons"][0])
            self.assertIn("implementer", result["decision"]["reasons"][0])

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

    def test_no_review_flag_keeps_one_backend_serving_both_roles(self) -> None:
        """The documented defaults reproduce the behaviour of an earlier run."""
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(ACCEPTABLE)")

            result = case.run(model="opus", effort="high", speed="fast")

            self.assertEqual("ACCEPTED", result["status"])
            runtime = result["runtime"]
            self.assertEqual("claude", runtime["actuator"])
            self.assertEqual("claude", runtime["review_actuator"])
            self.assertEqual(
                runtime["actuator_version"], runtime["review_actuator_version"]
            )
            self.assertEqual("opus", runtime["implementer_model"])
            self.assertEqual("claude-default", runtime["reviewer_model"])

            reviewer = result["inference"]["reviewer"]
            self.assertEqual(
                {
                    "backend": "claude",
                    "model": None,
                    "effort": None,
                    "speed": "standard",
                },
                reviewer["requested"],
            )
            # No effort, no settings document, no configuration override.
            self.assertEqual({}, reviewer["native"])
            command = result["review"]["meta"]["command"]
            self.assertNotIn("--effort", command)
            self.assertNotIn("--settings", command)
            self.assertNotIn("--model", command)
            self.assertIn("--safe-mode", command)
            self.assertEqual("json", command[command.index("--output-format") + 1])

    def test_the_reviewer_runs_the_backend_and_profile_it_was_given(self) -> None:
        frozen: list[dict] = []
        review = codex.run_reviewer

        def observe(**arguments):
            # The record already on disk when the reviewer is about to start.
            run_dir = arguments["out_dir"].parent
            frozen.append(
                json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
            )
            return review(**arguments)

        with tempfile.TemporaryDirectory() as temp:
            case, env = self._codex_case(
                Path(temp), efforts=["low", "medium", "high"], fast=False
            )

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
            self.assertEqual("claude-default", runtime["implementer_model"])
            self.assertEqual("gpt-5.6-sol", runtime["reviewer_model"])

            implementer = result["inference"]["implementer"]
            reviewer = result["inference"]["reviewer"]
            # One backend's inventory never answers for the other's.
            self.assertEqual("claude", implementer["requested"]["backend"])
            self.assertEqual("unverified", implementer["validation"]["status"])
            self.assertEqual(
                {
                    "backend": "codex",
                    "model": "gpt-5.6-sol",
                    "effort": "high",
                    "speed": "standard",
                },
                reviewer["requested"],
            )
            self.assertEqual("supported", reviewer["validation"]["status"])
            self.assertEqual(
                "backend-cache", reviewer["validation"]["inventory_source"]
            )
            # The keys the reviewer command carried, under the backend's names.
            self.assertEqual({"model_reasoning_effort": "high"}, reviewer["native"])
            # The reviewer effort reached the reviewer alone.
            self.assertEqual({}, implementer["native"])
            # Nothing requested is copied into what the backend reported.
            self.assertEqual(
                {"model": None, "effort": None, "speed": None}, reviewer["observed"]
            )
            self.assertEqual("incomplete", reviewer["provenance"])

            argv = self._reviewer_argv(result["run_dir"])
            self.assertEqual(["codex", "exec"], argv[:2])
            self.assertIn("--ignore-user-config", argv)
            self.assertIn("--output-schema", argv)
            self.assertEqual(
                ["model_reasoning_effort=high"],
                [argv[index + 1] for index, item in enumerate(argv) if item == "-c"],
            )
            self.assertNotIn("service_tier=priority", argv)

            # The confinement the reviewer runs under, recorded before it ran.
            isolation = frozen[0]["review"]["isolation"]
            self.assertEqual("macos-sandbox-exec", isolation["mechanism"])
            self.assertTrue(isolation["user_config_ignored"])
            self.assertEqual(
                set(result["review"]["isolation"]),
                {"mechanism", "denied_paths", "read_only_paths", "user_config_ignored"},
            )
            self.assertIn(
                result["worktree"], result["review"]["isolation"]["read_only_paths"]
            )
            self.assertNotIn("result", frozen[0]["review"])
            # The candidate is unchanged by a reviewer that could not write.
            self.assertFalse(Path(result["worktree"], "reviewer-write.txt").exists())

    def test_an_unsupported_reviewer_profile_ends_the_run_before_any_checkout(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case, env = self._codex_case(
                Path(temp), efforts=["low", "medium", "high"], fast=False
            )

            result = case.run(
                env=env,
                actuator="claude",
                review_actuator="codex",
                review_model="gpt-5.6-sol",
                review_effort="ultra",
            )

            self.assertEqual("REJECTED", result["status"])
            self.assertIsNone(result["worktree"])
            self.assertEqual([], result["iterations"])
            self.assertNotIn("baseline", result)
            self.assertNotIn("review", result)
            self.assertFalse(Path(result["run_dir"], "iterations").exists())
            self.assertEqual(1, len(result["decision"]["reasons"]))
            reason = result["decision"]["reasons"][0]
            self.assertIn("configuration error", reason)
            self.assertIn("reviewer", reason)

            evidence = json.loads(
                Path(result["run_dir"], "evidence.json").read_text(encoding="utf-8")
            )
            reviewer = evidence["inference"]["reviewer"]
            self.assertEqual("unsupported", reviewer["validation"]["status"])
            self.assertEqual(
                "backend-cache", reviewer["validation"]["inventory_source"]
            )
            self.assertIn("effort ultra", reviewer["validation"]["reason"])
            self.assertIsNone(reviewer["native"])
            # The implementer profile is untouched by the refusal of the other.
            implementer = evidence["inference"]["implementer"]
            self.assertEqual("unverified", implementer["validation"]["status"])
            self.assertIsNone(evidence["worktree"])

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

    def test_a_quick_gate_that_changed_the_candidate_ends_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                # The external sensor is the one gate the source repository
                # is never measured with, so the only tree it can move is the
                # candidate.
                constitution_text=constitution(sensor_command=MUTATING_SENSOR),
            )

            result = case.run()

            self.assertEqual("REJECTED", result["status"])
            # A control failure and not a failing gate: the decision says the
            # tree changed, and never that a gate returned something.
            self.assertEqual(
                ["quick gates changed the candidate workspace"],
                result["decision"]["reasons"],
            )
            iteration = result["iterations"][-1]
            self.assertTrue(all(g["passed"] for g in iteration["quick_gates"]))
            self.assertEqual(
                [0] * len(iteration["quick_gates"]),
                [g["exit_code"] for g in iteration["quick_gates"]],
            )
            self.assertTrue(iteration["scope"]["passed"])
            self.assertNotEqual(
                iteration["actuator_state"]["sha256"],
                iteration["observed_state"]["sha256"],
            )
            self.assertTrue(Path(result["worktree"], "mutant.py").is_file())
            self.assertNotIn("full_gates", result)
            self.assertNotIn("review", result)

    def test_a_full_gate_that_changed_the_candidate_ends_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                constitution_text=constitution(
                    sensor_command=MUTATING_SENSOR, sensor_phase="full"
                ),
            )

            result = case.run()

            self.assertEqual("REJECTED", result["status"])
            self.assertEqual(
                ["full gates changed the candidate workspace"],
                result["decision"]["reasons"],
            )
            self.assertTrue(all(g["passed"] for g in result["full_gates"]))
            self.assertEqual(
                [0] * len(result["full_gates"]),
                [g["exit_code"] for g in result["full_gates"]],
            )
            iteration = result["iterations"][-1]
            self.assertTrue(all(g["passed"] for g in iteration["quick_gates"]))
            self.assertTrue(iteration["scope"]["passed"])
            # The state the quick phase left, against the state the full gates
            # were measuring when they finished.
            self.assertNotEqual(
                iteration["observed_state"]["sha256"],
                result["full_gate_state"]["sha256"],
            )
            self.assertTrue(Path(result["worktree"], "mutant.py").is_file())
            self.assertNotIn("review", result)

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
            self.assertEqual(14, evidence["schema_version"])
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
        self.assertEqual(
            {"model": None, "effort": None, "speed": None}, reviewer["observed"]
        )
        self.assertEqual("incomplete", reviewer["provenance"])

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


class ExecutionEnvironmentE2ETests(unittest.TestCase):
    """A run that measures through a declared execution environment."""

    def _case(
        self,
        root: Path,
        *,
        task: str = PIXI_TASK,
        implementer: str = "implement(ACCEPTABLE)",
        constitution_text: str | None = None,
        **overrides,
    ) -> Case:
        return build_case(
            root,
            implementer=implementer,
            provider=True,
            constitution_text=(
                constitution(execution="default", quick_task=task)
                if constitution_text is None
                else constitution_text
            ),
            **overrides,
        )

    def _run(
        self, case: Case, log: Path, *, env: dict[str, str] | None = None, **overrides
    ) -> dict:
        return case.run(
            env={"CODESERVO_TEST_PIXI_LOG": str(log), **(env or {})}, **overrides
        )

    def _calls(self, log: Path) -> list[dict]:
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    def _invocations(self, log: Path) -> list[list[str]]:
        return [call["args"] for call in self._calls(log)]

    def _subcommands(self, log: Path) -> list[str]:
        return [call[0] for call in self._invocations(log)]

    def test_freezes_the_environment_and_measures_through_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            log = root / "pixi.log"

            result = self._run(case, log)

            self.assertEqual("ACCEPTED", result["status"])
            evidence = json.loads(Path(result["run_dir"], "evidence.json").read_text())
            environment = evidence["environment"]
            self.assertEqual("pixi", environment["provider"])
            self.assertEqual("0.77.1-test", environment["provider_version"])
            self.assertEqual("pyproject.toml", environment["manifest_path"])
            self.assertEqual("pixi.lock", environment["lock_path"])
            self.assertEqual("default", environment["environment"])
            self.assertEqual("test-platform", environment["platform"])
            self.assertEqual([PIXI_TASK], environment["declared_tasks"])
            # The digests are of the source repository at the base commit.
            self.assertEqual(
                sha256_file(case.repo / "pyproject.toml"),
                environment["manifest_sha256"],
            )
            self.assertEqual(
                sha256_file(case.repo / "pixi.lock"), environment["lock_sha256"]
            )
            stored = Path(result["run_dir"], environment["packages_path"])
            self.assertEqual("environment/packages.json", environment["packages_path"])
            self.assertEqual(PIXI_PACKAGES, json.loads(stored.read_text()))
            self.assertEqual(sha256_file(stored), environment["packages_sha256"])
            self.assertEqual(len(PIXI_PACKAGES), environment["package_count"])
            # Nothing the description says about the operator is recorded.
            for private in ("/operator", "cache_dir", "auth_dir", "config_locations"):
                self.assertNotIn(
                    private, Path(result["run_dir"], "evidence.json").read_text()
                )

    def test_each_gate_names_the_manifest_of_the_tree_it_measures(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            log = root / "pixi.log"

            result = self._run(case, log)

            def command(name: str, gates: list[dict]) -> str:
                return next(gate["command"] for gate in gates if gate["name"] == name)

            worktree = Path(result["worktree"])
            repo = Path(result["repo"])
            baseline = command("syntax", result["baseline"])
            quick = command("syntax", result["iterations"][-1]["quick_gates"])
            self.assertEqual(
                "pixi run --as-is --clean-env --no-config"
                f" --manifest-path '{repo / 'pyproject.toml'}'"
                f" --environment 'default' '{PIXI_TASK}'",
                baseline,
            )
            self.assertEqual(
                "pixi run --as-is --clean-env --no-config"
                f" --manifest-path '{worktree / 'pyproject.toml'}'"
                f" --environment 'default' '{PIXI_TASK}'",
                quick,
            )
            self.assertNotIn(str(worktree), baseline)
            self.assertNotIn(str(repo), quick)
            # A shell gate is built and run exactly as it is without a provider.
            self.assertEqual(
                COMPILE_COMMAND, command("full", result["full_gates"])
            )
            self.assertNotIn(
                "pixi",
                command("task-outcome", result["iterations"][-1]["quick_gates"]),
            )

    def test_never_asks_the_provider_to_write_the_lockfile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            log = root / "pixi.log"

            self._run(case, log)

            invocations = self._invocations(log)
            self.assertTrue(invocations)
            # The description is asked twice: once of the source, once of the
            # candidate whose directory the installation reports.
            self.assertEqual(
                ["list", "info", "info", "install"],
                [call[0] for call in invocations if call[0] != "run"],
            )

    def test_refuses_a_lockfile_that_disagrees_with_the_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root, stale_lock=True)
            log = root / "pixi.log"
            manifest = sha256_file(case.repo / "pyproject.toml")
            lock = sha256_file(case.repo / "pixi.lock")

            result = self._run(case, log)

            self.assertEqual("REJECTED", result["status"])
            self.assertEqual(1, len(result["decision"]["reasons"]))
            self.assertIn("pixi.lock", result["decision"]["reasons"][0])
            # Before the baseline, and before any checkout.
            self.assertNotIn("baseline", result)
            self.assertIsNone(result["worktree"])
            self.assertEqual([], result["iterations"])
            # The frozen control input is byte-identical afterwards.
            self.assertEqual(manifest, sha256_file(case.repo / "pyproject.toml"))
            self.assertEqual(lock, sha256_file(case.repo / "pixi.lock"))
            self.assertEqual(["list"], self._subcommands(log))
            environment = result["environment"]
            self.assertEqual(manifest, environment["manifest_sha256"])
            self.assertNotIn("packages_path", environment)

    def test_refuses_a_task_the_environment_does_not_declare(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root, task="absent-task")
            log = root / "pixi.log"

            result = self._run(case, log)

            self.assertEqual("REJECTED", result["status"])
            self.assertIn("absent-task", result["decision"]["reasons"][0])
            self.assertNotIn("baseline", result)
            self.assertIsNone(result["worktree"])
            # No provider task ever ran, and nothing was installed.
            self.assertEqual(["list", "info"], self._subcommands(log))

    def test_installs_the_candidate_after_the_checkout_and_before_the_agent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(
                root,
                implementer="""
                (worktree / "prepared.txt").write_text(
                    "yes"
                    if (worktree / ".pixi" / "envs" / "default").is_dir()
                    else "no"
                )
                implement(ACCEPTABLE)
                """,
            )
            log = root / "pixi.log"

            result = self._run(case, log)

            self.assertEqual("ACCEPTED", result["status"])
            worktree = Path(result["worktree"])
            # The environment existed before the actuator was ever started.
            self.assertEqual(
                "yes", (worktree / "prepared.txt").read_text(encoding="utf-8")
            )
            install = next(
                call for call in self._invocations(log) if call[0] == "install"
            )
            # Into the checkout, and never into the source repository.
            self.assertEqual(1, self._subcommands(log).count("install"))
            self.assertIn(str(worktree / "pyproject.toml"), install)
            self.assertNotIn(str(Path(result["repo"]) / "pyproject.toml"), install)
            # The operator's environment is left exactly as it was found.
            source_prefix = Path(result["repo"], ".pixi", "envs", "default")
            self.assertEqual([], list(source_prefix.iterdir()))
            subcommands = self._subcommands(log)
            # After the checkout: the baseline already measured the source.
            self.assertLess(subcommands.index("run"), subcommands.index("install"))

    def test_records_what_the_installation_did(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            log = root / "pixi.log"

            result = self._run(case, log)

            worktree = Path(result["worktree"])
            candidate = result["environment"]["candidate"]
            self.assertEqual(
                {
                    "prefix_path",
                    "command",
                    "exit_code",
                    "duration_ms",
                    "manifest_sha256",
                    "lock_sha256",
                    "config_sha256",
                    "unchanged_at_end",
                },
                set(candidate),
            )
            self.assertEqual(
                [
                    "pixi",
                    "install",
                    "--locked",
                    "--no-config",
                    "--environment",
                    "default",
                    "--manifest-path",
                    str(worktree / "pyproject.toml"),
                ],
                candidate["command"],
            )
            self.assertEqual(0, candidate["exit_code"])
            self.assertGreaterEqual(candidate["duration_ms"], 0)
            self.assertEqual(
                str(worktree / ".pixi" / "envs" / "default"), candidate["prefix_path"]
            )
            self.assertTrue(Path(candidate["prefix_path"]).is_dir())
            # The digests are of the candidate, and the workspace never moved.
            self.assertEqual(
                sha256_file(worktree / "pyproject.toml"), candidate["manifest_sha256"]
            )
            self.assertEqual(
                sha256_file(worktree / "pixi.lock"), candidate["lock_sha256"]
            )
            self.assertIsNone(candidate["config_sha256"])
            self.assertTrue(candidate["unchanged_at_end"])
            evidence = json.loads(Path(result["run_dir"], "evidence.json").read_text())
            self.assertFalse(
                Path(evidence["environment"]["candidate"]["prefix_path"]).is_absolute()
            )

    def test_a_refused_installation_ends_the_run_before_any_actuation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            log = root / "pixi.log"

            result = self._run(
                case, log, env={"CODESERVO_TEST_PIXI_INSTALL_FAILS": "1"}
            )

            self.assertEqual("REJECTED", result["status"])
            self.assertEqual(1, len(result["decision"]["reasons"]))
            reason = result["decision"]["reasons"][0]
            self.assertIn("execution environment", reason)
            self.assertIn("default", reason)
            # Nothing actuated, and no measurement ran in the candidate.
            self.assertEqual([], result["iterations"])
            self.assertNotIn("full_gates", result)
            candidate = result["environment"]["candidate"]
            self.assertEqual(1, candidate["exit_code"])
            self.assertFalse(Path(candidate["prefix_path"]).exists())
            self.assertEqual(
                ["list", "info", "run", "info", "install"],
                self._subcommands(log),
            )

    def test_refuses_a_source_repository_without_the_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root, source_environment=False)
            log = root / "pixi.log"

            result = self._run(case, log)

            self.assertEqual("REJECTED", result["status"])
            reason = result["decision"]["reasons"][0]
            self.assertIn("environment default is not installed", reason)
            self.assertIn(str(case.repo / ".pixi" / "envs" / "default"), reason)
            # Before the baseline gates, and before any checkout.
            self.assertNotIn("baseline", result)
            self.assertIsNone(result["worktree"])
            self.assertEqual(["list", "info"], self._subcommands(log))
            # The controller wrote nothing into the operator's tree.
            self.assertFalse((case.repo / ".pixi").exists())

    def test_no_measurement_can_resolve_or_install(self) -> None:
        forbidden = (
            "test xtruetruetrue = \\\"x$PIXI_OFFLINE$PIXI_NO_INSTALL$PIXI_FROZEN\\\""
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(
                root,
                constitution_text=constitution(
                    execution="default",
                    quick_task=PIXI_TASK,
                    full_command=forbidden,
                    sensor_command=None,
                ),
            )
            log = root / "pixi.log"

            result = self._run(case, log)

            # A shell gate of a provider run is a measurement too.
            self.assertEqual("ACCEPTED", result["status"])
            measured = [call for call in self._calls(log) if call["args"][0] == "run"]
            self.assertTrue(measured)
            for call in measured:
                self.assertEqual(
                    {
                        "PIXI_OFFLINE": "true",
                        "PIXI_NO_INSTALL": "true",
                        "PIXI_FROZEN": "true",
                    },
                    call["env"],
                )
            installed = [
                call for call in self._calls(log) if call["args"][0] == "install"
            ]
            # The installation is not a measurement: none of the three reaches
            # it, or it would install nothing and still report success.
            self.assertEqual([{}], [call["env"] for call in installed])

    def test_a_candidate_file_that_changed_is_a_control_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(
                root,
                implementer="""
                implement(ACCEPTABLE)
                (worktree / "pixi.lock").write_text(
                    "version: 6\\nenvironments: {}\\n# resolved again\\n"
                )
                """,
            )
            log = root / "pixi.log"

            result = self._run(case, log)

            self.assertEqual("REJECTED", result["status"])
            self.assertEqual(
                ["execution environment: pixi.lock changed during the run"],
                result["decision"]["reasons"],
            )
            # A control failure and not a failing gate: every gate passed.
            iteration = result["iterations"][-1]
            self.assertTrue(all(g["passed"] for g in iteration["quick_gates"]))
            self.assertTrue(iteration["scope"]["passed"])
            self.assertNotIn("full_gates", result)
            self.assertFalse(result["environment"]["candidate"]["unchanged_at_end"])

    def test_the_environment_directory_is_never_a_candidate_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            log = root / "pixi.log"

            result = self._run(case, log)

            self.assertEqual("ACCEPTED", result["status"])
            worktree = Path(result["worktree"])
            self.assertTrue((worktree / ".pixi" / "envs" / "default").is_dir())
            scope = result["iterations"][-1]["scope"]
            self.assertEqual(["app.py"], scope["details"]["changed_files"])
            self.assertEqual([], scope["details"]["violations"])
            self.assertEqual(2, scope["details"]["diff_lines"])
            patch_text = Path(result["run_dir"], "change.patch").read_text()
            observed = Path(
                result["run_dir"], result["iterations"][-1]["observed_state"]["path"]
            ).read_text()
            for text in (patch_text, observed):
                self.assertNotIn(".pixi", text)

    def test_each_confinement_names_the_tree_it_measures(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            log = root / "pixi.log"

            result = self._run(case, log)

            self.assertEqual("ACCEPTED", result["status"])
            repo = Path(result["repo"])
            worktree = Path(result["worktree"])
            source = result["gate_isolation"]["source"]
            candidate = result["gate_isolation"]["candidate"]
            # A gate reads the metadata and the environment of the tree it
            # measures, writes neither, and is never handed the other tree.
            self.assertEqual(
                [result["run_dir"], str(repo / ".git"), str(repo / ".pixi")],
                source["read_only_paths"],
            )
            self.assertEqual(
                [result["run_dir"], str(worktree / ".git"), str(worktree / ".pixi")],
                candidate["read_only_paths"],
            )
            for document in (source, candidate):
                self.assertEqual("macos-sandbox-exec", document["mechanism"])
                self.assertEqual([], document["denied_paths"])
            self.assertFalse(
                [path for path in source["read_only_paths"][1:] if str(worktree) in path]
            )
            self.assertFalse(
                [path for path in candidate["read_only_paths"][1:] if str(repo) in path]
            )
            # The actuator reads both, and writes neither.
            actuator = result["actuator_isolation"]
            self.assertEqual(
                [str(repo), str(worktree / ".git"), str(worktree / ".pixi")],
                actuator["read_only_paths"],
            )
            for protected in (worktree / ".git", worktree / ".pixi"):
                self.assertNotIn(str(protected), actuator["denied_paths"])
            self.assertTrue((worktree / ".pixi").is_dir())
            # The reviewer's confinement is unchanged: the whole worktree.
            self.assertEqual(
                [str(repo), str(worktree)],
                result["review"]["isolation"]["read_only_paths"],
            )

    def test_a_run_without_a_provider_never_invokes_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = build_case(
                root, implementer="implement(ACCEPTABLE)", provider=True
            )
            log = root / "pixi.log"

            result = self._run(case, log)

            self.assertEqual("ACCEPTED", result["status"])
            self.assertEqual({"provider": "none"}, result["environment"])
            self.assertEqual([], self._calls(log))
            self.assertNotIn("candidate", result["environment"])
            self.assertFalse((Path(result["worktree"]) / ".pixi").exists())


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(
    sys.platform == "darwin",
    "controller confinement requires macOS sandbox-exec",
)
class RunJournalE2ETests(unittest.TestCase):
    def journal(self, result: dict) -> list[dict]:
        return read_journal(Path(result["run_dir"], JOURNAL_NAME))

    def evidence(self, result: dict) -> dict:
        return json.loads(
            Path(result["run_dir"], "evidence.json").read_text(encoding="utf-8")
        )

    def test_the_journal_covers_the_trajectory_in_the_order_it_happened(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer=CONVERGING_IMPLEMENTER)

            result = case.run()

            self.assertEqual("ACCEPTED", result["status"])
            recorded = [event["type"] for event in self.journal(result)]
            self.assertEqual("run.started", recorded[0])
            self.assertEqual("run.finished", recorded[-1])
            self.assertEqual("decision.recorded", recorded[-2])
            self.assertLessEqual(
                {
                    "run.started",
                    "inputs.frozen",
                    "inference.profiles_frozen",
                    "baseline.finished",
                    "workspace.ready",
                    "actuator.started",
                    "actuator.finished",
                    "actuator.profile_observed",
                    "gate.finished",
                    "feedback.emitted",
                    "review.finished",
                    "review.profile_observed",
                    "decision.recorded",
                    "run.finished",
                },
                set(recorded),
            )
            # A run declaring no execution provider takes neither transition.
            self.assertNotIn("environment.validated", recorded)
            self.assertNotIn("environment.prepared", recorded)
            self.assertNotIn("budget.exhausted", recorded)
            self.assertLess(
                recorded.index("inputs.frozen"), recorded.index("baseline.finished")
            )
            self.assertLess(
                recorded.index("baseline.finished"), recorded.index("workspace.ready")
            )
            self.assertLess(
                recorded.index("workspace.ready"), recorded.index("actuator.started")
            )
            self.assertLess(
                recorded.index("feedback.emitted"), recorded.index("review.finished")
            )

    def test_one_gate_event_per_gate_result_the_record_holds(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer=CONVERGING_IMPLEMENTER)

            result = case.run()

            measured = [
                ("baseline", gate) for gate in result["baseline"]
            ]
            for iteration in result["iterations"]:
                measured += [("quick", gate) for gate in iteration["quick_gates"]]
            measured += [("full", gate) for gate in result["full_gates"]]
            recorded = [
                event["payload"]
                for event in self.journal(result)
                if event["type"] == "gate.finished"
            ]

            self.assertEqual(len(measured), len(recorded))
            self.assertEqual(
                [
                    {
                        "phase": phase,
                        "name": gate["name"],
                        "passed": gate["passed"],
                        "result_sha256": gate["result_sha256"],
                    }
                    for phase, gate in measured
                ],
                recorded,
            )

    def test_the_events_block_describes_the_complete_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(ACCEPTABLE)")

            result = case.run()

            evidence = self.evidence(result)
            events = self.journal(result)
            block = evidence["events"]
            self.assertEqual(14, evidence["schema_version"])
            self.assertEqual(
                {"path", "count", "head_sha256", "file_sha256"}, set(block)
            )
            self.assertEqual(JOURNAL_NAME, block["path"])
            self.assertEqual(len(events), block["count"])
            self.assertEqual(events[-1]["sha256"], block["head_sha256"])
            self.assertEqual(
                sha256_file(Path(result["run_dir"], JOURNAL_NAME)),
                block["file_sha256"],
            )

    def test_the_decision_is_an_event_before_it_is_a_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                reviewer='emit_review({"criteria": [], "findings": []})',
            )

            result = case.run()

            self.assertEqual("REJECTED", result["status"])
            events = self.journal(result)
            self.assertEqual("decision.recorded", events[-2]["type"])
            self.assertEqual(
                {"status": "REJECTED", "reasons": result["decision"]["reasons"]},
                events[-2]["payload"],
            )
            self.assertEqual({"status": "REJECTED"}, events[-1]["payload"])

    def test_a_run_rejected_at_the_baseline_still_closes_its_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                constitution_text=constitution(quick_command="false"),
            )

            result = case.run()

            self.assertEqual("REJECTED", result["status"])
            self.assertIsNone(result["worktree"])
            recorded = [event["type"] for event in self.journal(result)]
            self.assertEqual(
                [
                    "run.started",
                    "inputs.frozen",
                    "inference.profiles_frozen",
                    "gate.finished",
                    "gate.finished",
                    "baseline.finished",
                    "decision.recorded",
                    "run.finished",
                ],
                recorded,
            )
            self.assertEqual("VALID", verify_run(Path(result["run_dir"]))["status"])

    def test_a_run_that_never_converges_records_the_budget_that_ended_it(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer="implement(UNACCEPTABLE)")

            result = case.run(max_iterations=2)

            self.assertEqual("REJECTED", result["status"])
            recorded = [event["type"] for event in self.journal(result)]
            self.assertEqual(
                ["budget.exhausted", "decision.recorded", "run.finished"],
                recorded[-3:],
            )

    def test_a_run_declaring_a_provider_records_both_environment_transitions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                provider=True,
                constitution_text=constitution(
                    execution="default", quick_task=PIXI_TASK
                ),
            )

            result = case.run()

            self.assertEqual("ACCEPTED", result["status"])
            recorded = [event["type"] for event in self.journal(result)]
            self.assertLess(
                recorded.index("environment.validated"),
                recorded.index("baseline.finished"),
            )
            self.assertLess(
                recorded.index("workspace.ready"),
                recorded.index("environment.prepared"),
            )
            self.assertLess(
                recorded.index("environment.prepared"),
                recorded.index("actuator.started"),
            )
            # The two declared files belong to the source repository, so a
            # verification reading the run directory alone still passes.
            self.assertEqual("VALID", verify_run(Path(result["run_dir"]))["status"])

    def test_a_gate_reads_every_transition_that_preceded_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                constitution_text=constitution(
                    sensor_command=f"{sys.executable} journal_probe.py"
                ),
            )
            (case.repo / "journal_probe.py").write_text(
                JOURNAL_PROBE, encoding="utf-8"
            )
            commit_repository(case.repo, "journal probe")

            result = case.run()

            self.assertEqual("ACCEPTED", result["status"])
            observed = Path(
                result["run_dir"],
                "iterations/01/quick/task-outcome.stdout.log",
            ).read_text(encoding="utf-8")
            for transition in (
                "run.started",
                "inputs.frozen",
                "baseline.finished",
                "workspace.ready",
                "actuator.finished",
            ):
                self.assertIn(transition, observed)

    def test_the_reviewer_result_is_recorded_as_the_reviewer_produced_it(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(
                Path(temp),
                implementer="implement(ACCEPTABLE)",
                reviewer=LOCATING_REVIEWER,
            )

            result = case.run()

            self.assertEqual("ACCEPTED", result["status"])
            evidence = self.evidence(result)
            review = evidence["review"]
            located = review["result"]["findings"][0]["path"]
            self.assertTrue(Path(located).is_absolute())
            self.assertEqual(str(Path(result["worktree"], "app.py")), located)
            # The digest was taken over what the reviewer returned, so it
            # recomputes from the document the run left behind.
            self.assertEqual(sha256_json(review["result"]), review["result_sha256"])
            self.assertEqual("VALID", verify_run(Path(result["run_dir"]))["status"])

    def test_a_verified_run_is_valid_and_a_moved_digest_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = build_case(Path(temp), implementer=CONVERGING_IMPLEMENTER)

            result = case.run()
            run_dir = Path(result["run_dir"])
            report = verify_run(run_dir)

            self.assertEqual("VALID", report["status"])
            self.assertEqual([], report["failures"])
            self.assertEqual([], report["missing"])

            record = json.loads(
                (run_dir / "evidence.json").read_text(encoding="utf-8")
            )
            record["status"] = "REJECTED"
            (run_dir / "evidence.json").write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            edited = verify_run(run_dir)

            self.assertEqual("INVALID", edited["status"])
            self.assertTrue(
                all(JOURNAL_NAME in failure for failure in edited["failures"])
            )
