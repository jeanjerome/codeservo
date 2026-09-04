"""Capture what a run record looks like, so a refactor can be held to it.

A structural change must leave `evidence.json` alone. This drives seven
trajectories through the control loop with a scripted backend and captures,
for each one, the shape of the record, the sequence of events, the artefacts
the run directory holds and the verdict `verify-run` reaches.

Values decided by the clock, by a temporary location or by a digest of either
are replaced by their type or masked, so two captures taken from different
revisions compare on everything a refactor must not move.

    python tools/record_parity.py capture before.json
    ... change the code ...
    python tools/record_parity.py capture after.json
    python tools/record_parity.py compare before.json after.json

This is a maintainer tool, not a gate: it compares two revisions, where a gate
states a property of one.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from codeservo.evidence.verify import verify_run  # noqa: E402
from harness import PIXI_TASK, build_case, constitution  # noqa: E402

# Scalars the host, the clock or a temporary path decides. The confinement
# mechanism is one of them: the profile is the record's, the mechanism applying
# it is whichever one the host carries, so pinning its name would make this a
# measurement of one operating system.
VOLATILE = frozenset(
    {
        "run_id", "started_at", "finished_at", "duration_ms", "repo", "state_dir",
        "run_dir", "worktree", "base_commit", "path", "prefix_path", "command",
        "stdout_path", "stderr_path", "packages_path", "sha256", "stdout_sha256",
        "stderr_sha256", "result_sha256", "meta_sha256", "observations_sha256",
        "patch_sha256", "manifest_sha256", "lock_sha256", "config_sha256",
        "packages_sha256", "head_sha256", "file_sha256", "constitution_sha256",
        "catalogue_sha256",
        "codeservo_commit",
        "actuator_version", "review_actuator_version", "python_version",
        "git_version", "session_id", "stdout_tail", "stderr_tail", "text",
        "summary", "details", "evidence", "cost_usd", "duration",
        "provider_version", "platform", "mechanism", "declared_tasks",
        "profile_sha256",
        "message", "reference", "codeservo_version", "num_turns",
        "total_cost_usd", "usage",
    }
)

RUN_IDENTIFIER = re.compile(r"\d{8}T\d{6}\d{6}Z")


def shape(value: object, key: str | None = None) -> object:
    """The document with every volatile scalar reduced to its type or masked."""
    if key in VOLATILE:
        return f"<{type(value).__name__}>"
    if isinstance(value, dict):
        return {name: shape(item, name) for name, item in sorted(value.items())}
    if isinstance(value, list):
        return [shape(item, key) for item in value]
    if isinstance(value, str):
        return RUN_IDENTIFIER.sub("<run-id>", value)
    return value


ACCEPTS = "implement(ACCEPTABLE)"
CONVERGES = """
if (worktree / "app.py").read_text().strip().endswith("return 1"):
    implement(ACCEPTABLE)
else:
    implement(UNACCEPTABLE)
"""
NEVER = "implement(UNACCEPTABLE)"
REJECTING_REVIEW = """
emit_review({
    "criteria": [{"id": "AC1", "status": "unsatisfied", "evidence": "no"}],
    "findings": [
        {"severity": "blocker", "message": "m", "path": "app.py", "line": 1}
    ],
})
"""
UNVERIFIABLE_REVIEW = """
emit_review({
    "criteria": [{"id": "AC1", "status": "not_verifiable", "evidence": "none"}],
    "findings": [],
})
"""
COUNTING = """
count = worktree / "attempts.txt"
attempts = int(count.read_text()) + 1 if count.exists() else 1
count.write_text(str(attempts))
implement(ACCEPTABLE)
"""
OBJECTS_ONCE = """
count = worktree / "attempts.txt"
if int(count.read_text()) >= 2:
    emit_review(SATISFIED)
else:
    emit_review({
        "criteria": [{"id": "AC1", "status": "not_satisfied", "evidence": "no"}],
        "findings": [
            {"severity": "major", "message": "m", "path": "app.py", "line": 1}
        ],
    })
"""

# One trajectory per way a run can end, plus one measuring through a provider.
TRAJECTORIES = (
    ("accepted", {"implementer": ACCEPTS}),
    ("converges", {"implementer": CONVERGES}),
    ("never-converges", {"implementer": NEVER}),
    ("review-rejects", {"implementer": ACCEPTS, "reviewer": REJECTING_REVIEW}),
    ("review-corrects", {"implementer": COUNTING, "reviewer": OBJECTS_ONCE}),
    ("review-escalates", {"implementer": ACCEPTS, "reviewer": UNVERIFIABLE_REVIEW}),
    (
        "provider",
        {
            "implementer": ACCEPTS,
            "provider": True,
            "constitution_text": constitution(
                execution="default", quick_task=PIXI_TASK
            ),
        },
    ),
)


def drive(name: str, root: Path, **options: object) -> dict:
    case = build_case(root, **options)  # type: ignore[arg-type]
    result = case.run()
    run_dir = Path(result["run_dir"])
    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {
        "case": name,
        "status": result["status"],
        "reasons": result["decision"]["reasons"],
        "record": shape(json.loads((run_dir / "evidence.json").read_text("utf-8"))),
        "event_types": [event["type"] for event in events],
        "event_payload_keys": [sorted(event["payload"]) for event in events],
        "artifacts": sorted(
            str(path.relative_to(run_dir))
            for path in run_dir.rglob("*")
            if path.is_file()
        ),
        "verify_status": verify_run(run_dir)["status"],
        "verify_checks": shape(verify_run(run_dir)),
    }


def capture(destination: Path) -> int:
    captured = []
    for name, options in TRAJECTORIES:
        with tempfile.TemporaryDirectory() as root:
            captured.append(drive(name, Path(root), **options))
        print(f"{name}: {captured[-1]['status']} / {captured[-1]['verify_status']}")
    destination.write_text(json.dumps(captured, indent=2, sort_keys=True), "utf-8")
    print(f"captured {len(captured)} trajectories into {destination}")
    return 0


def compare(before: Path, after: Path) -> int:
    """Say what moved between two captures, trajectory by trajectory.

    Trajectories are paired by name and never by position, so one added to
    the list is reported as added rather than compared with whichever one now
    stands where it stood.
    """
    left = {item["case"]: item for item in json.loads(before.read_text("utf-8"))}
    right = {item["case"]: item for item in json.loads(after.read_text("utf-8"))}
    if left == right:
        print("the record is unchanged across every captured trajectory")
        return 0
    for name in sorted(left.keys() - right.keys()):
        print(f"{name}: removed")
    for name in sorted(right.keys() - left.keys()):
        print(f"{name}: added")
    for name, one in left.items():
        other = right.get(name)
        if other is None:
            continue
        moved = [key for key in one if one[key] != other.get(key)]
        if moved:
            print(f"{name}: {', '.join(moved)}")
    return 1


def main() -> int:
    arguments = sys.argv[1:]
    if len(arguments) == 2 and arguments[0] == "capture":
        return capture(Path(arguments[1]))
    if len(arguments) == 3 and arguments[0] == "compare":
        return compare(Path(arguments[1]), Path(arguments[2]))
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
