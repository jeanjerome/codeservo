# CodeServo

**AI changes the code. The controller decides whether it converged.**

CodeServo is a deliberately small experiment in **Software Engineering as a Control System**.
It does not try to be another coding agent. It wraps a coding agent in a deterministic control loop whose source of truth is the target repository.

V0 tests one proposition:

> Can a deterministic controller drive an AI coding actuator toward an acceptable repository state using repository-owned invariants and mechanical feedback?

## V0 loop

```text
TASK.md
   ↓
clean repository + frozen constitution
   ↓
baseline gates
   ↓
isolated Git worktree
   ↓
Codex implementer
   ↓
scope sensor + quick gates
   │ fail
   └──────────── feedback ────────────↺
   ↓ pass
full gates
   ↓
independent read-only Codex review sensor
   ↓
mechanical decision
   ↓
evidence.json + change.patch
   ↓
ACCEPTED / REJECTED
```

The implementer never gets to mark itself done. `ACCEPTED` is computed by CodeServo from explicit evidence.

## Deliberate V0 limits

- One actuator: Codex CLI.
- One target repository per run.
- One bounded implementation loop.
- No auto-commit, auto-merge, PR creation, queue, daemon, UI, memory, multi-agent planning, or learning.
- Full gates run once after quick convergence. A full-gate failure rejects the candidate in V0.
- Review is semantic evidence, not an opaque verdict: the reviewer returns criterion statuses and findings; CodeServo computes the decision.

## Requirements

- Python 3.12+
- Git
- Codex CLI installed and authenticated
- A clean target Git repository

## Install for development

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m unittest discover -s tests -v
```

## Prepare a target repository

```bash
cd /path/to/project
codeservo init
```

Then edit `.codeservo/constitution.toml`. It is the repository's stable executable constitution.

Example:

```toml
version = 1

[scope]
protected = [".codeservo/**", ".github/workflows/**"]
max_changed_files = 20
max_diff_lines = 800

[[gate]]
name = "lint"
phase = "quick"
command = "make lint"
timeout_seconds = 120
baseline = true

[[gate]]
name = "unit"
phase = "quick"
command = "make test-unit"
timeout_seconds = 300
baseline = true

[[gate]]
name = "full"
phase = "full"
command = "make check"
timeout_seconds = 900
baseline = true

[review]
blocking_severities = ["blocker", "major"]
```

Commit the constitution. CodeServo refuses to start from a dirty source repository, freezes the constitution at run start, and treats changes under protected paths as control errors.

## Write one task

```markdown
# Task

## Goal
Add a health endpoint.

## Acceptance criteria
- [AC1] `GET /health` returns HTTP 200.
- [AC2] The response body is `{"status":"ok"}`.
- [AC3] Existing API tests remain green.

## Out of scope
- Authentication changes.
- Dependency upgrades.
```

Acceptance criterion ids are mandatory because the reviewer must account for every criterion and the controller, not the reviewer, computes acceptance.

## Run

```bash
codeservo run --repo /path/to/project --task ./TASK.md
```

Optional:

```bash
codeservo run \
  --repo /path/to/project \
  --task ./TASK.md \
  --state-dir /path/to/codeservo-state \
  --max-iterations 4 \
  --model <implementer-model> \
  --review-model <review-model>
```

`--state-dir` selects where controller-owned evidence and temporary worktrees are stored. It defaults to `~/.codeservo` and must be outside the target repository.

A run produces immutable-ish external artifacts under the selected state directory:

```text
<state-dir>/
├── worktrees/<repo>/<run-id>/
└── runs/<repo>/<run-id>/
    ├── TASK.md
    ├── constitution.toml
    ├── baseline/
    ├── iterations/
    │   ├── 01/
    │   │   ├── input.patch
    │   │   ├── prompt.md
    │   │   ├── agent/
    │   │   ├── actuator.patch
    │   │   ├── quick/
    │   │   ├── observed.patch
    │   │   └── controller-feedback.md  # only when sensors fail
    │   └── ...
    ├── full/
    ├── review/
    ├── change.patch
    └── evidence.json
```

The evidence directory is outside the target worktree so the actuator cannot rewrite the controller's record. `evidence.json` is checkpointed during the run. Each iteration records the exact feedback received, prompt hash, repository state before and after actuation, repository state observed after quick gates, and any feedback generated for the next iteration.

Gates marked `baseline=false` are independent acceptance sensors. The implementer prompt instructs the actuator not to inspect or run them; their output reaches the actuator only through controller feedback.

## Mechanical acceptance rule

A candidate is `ACCEPTED` only when all of the following hold:

1. The original repository baseline was green.
2. The source repository was and remained clean during baseline.
3. Scope invariants pass.
4. Every quick gate passes within the iteration budget.
5. Every full gate passes.
6. The independent review returns exactly every acceptance criterion as `satisfied`.
7. The review contains no finding whose severity is configured as blocking.

Anything else is `REJECTED`.

## Why this shape

The target repository owns the desired operating envelope (`.codeservo/constitution.toml`). `TASK.md` supplies a temporary desired delta. Codex is only an actuator. Tests, linters, scope constraints and independent semantic review are sensors. CodeServo is the controller. Git is the state substrate. `evidence.json` is the audit record.

That is enough to test V0 without prematurely building a software factory.
