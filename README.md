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
isolated shallow Git checkout
   ↓
actuator implementer
   ↓
scope sensor + quick gates
   │ fail
   └──────────── feedback ────────────↺
   ↓ pass
full gates
   ↓
independent read-only review sensor
   ↓
mechanical decision
   ↓
evidence.json + change.patch
   ↓
ACCEPTED / REJECTED
```

The implementer never gets to mark itself done. `ACCEPTED` is computed by CodeServo from explicit evidence.

`FINDINGS.md` records what the experiments run through this controller
established, what they did not, and what follows for its design.

## Deliberate V0 limits

- One actuator per run: Claude Code or Codex CLI.
- One target repository per run.
- One bounded implementation loop.
- No auto-commit, auto-merge, PR creation, queue, daemon, UI, memory, multi-agent planning, or learning.
- Full gates run once after quick convergence. A full-gate failure rejects the candidate in V0.
- Review is semantic evidence, not an opaque verdict: the reviewer returns criterion statuses and findings; CodeServo computes the decision.

## Requirements

- Python 3.12+
- Git
- Claude Code or Codex CLI installed and authenticated
- A clean target Git repository
- macOS `sandbox-exec`: the controller confines every actuator process

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

[[gate]]
name = "acceptance"
phase = "quick"
command = "pytest -q \"$CODESERVO_SENSOR_PATH\""
timeout_seconds = 300
baseline = false
sensor = "my-project/health-contract"

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
  --actuator claude \
  --max-iterations 4 \
  --model <implementer-model> \
  --review-model <review-model>
```

## Actuators

`--actuator` selects the agent CLI that proposes and reviews the change. It
accepts `claude` and `codex`, defaults to `$CODESERVO_ACTUATOR`, and falls back
to `claude`. `--model` and `--review-model` are passed through to that CLI, so
their accepted values are the ones the selected CLI accepts.

Both backends run without session persistence and without MCP servers, so a run
depends on the frozen task, the frozen constitution, the controller feedback,
and the repository content. Claude Code additionally runs with user memory,
settings, hooks, skills and custom commands disabled; plugin and subagent
definitions installed on the machine stay registered, but the actuator tool set
excludes the tools that would reach them.

| | implementer | reviewer |
| --- | --- | --- |
| `claude` | `--safe-mode`, tools limited to file and shell access, permissions bypassed inside the controller profile | same, tools limited to `Bash,Read`, structured output validated against the review schema |
| `codex` | `--ephemeral --ignore-user-config` | same, plus the frozen output schema |

Because macOS refuses to apply a seatbelt profile inside another one, a confined
Codex process runs with `--sandbox danger-full-access` and the controller
profile becomes its only confinement. Claude Code never sandboxes itself, so the
controller profile is always its only confinement.

## State directory

`--state-dir` selects where controller-owned sensors, evidence, and temporary
working trees are stored. It defaults to `~/.codeservo` and must be outside the
target repository. Sensor references in the constitution resolve below
`<state-dir>/sensors/`.

A run produces immutable-ish external artifacts under the selected state directory:

```text
<state-dir>/
├── sensors/<repo>/<sensor>/
├── worktrees/<repo>/<run-id>/
└── runs/<repo>/<run-id>/
    ├── TASK.md
    ├── constitution.toml
    ├── sensors/                 # frozen sensor snapshot
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

The evidence directory is outside the target worktree so the actuator cannot
rewrite the controller's record. `evidence.json` is checkpointed during the
run. Each iteration records the exact feedback received, prompt hash,
repository state before and after actuation, repository state observed after
quick gates, and any feedback generated for the next iteration.
Paths stored in `evidence.json` are relative to the run directory so a copied
run remains self-contained. The record includes the CodeServo version and
source commit plus the actuator name and version, the requested models, and the
Python and Git versions. Each Claude Code session additionally records the model
identifier it resolved to and the tokens every model spent, because a model
alias moves over time while two runs have to stay comparable. A reviewer session
reports its usage but no resolved identifier, since its single-object output
carries no session header. SHA-256 digests cover frozen sensors, patch snapshots,
prompts, feedback, gate outcomes and logs, agent events and messages, and
reviewer artifacts.

Gates marked `baseline=false` must reference an external acceptance sensor.
CodeServo freezes the sensor and its digest before baseline verification, then
provides its path only to the gate process through `CODESERVO_SENSOR_PATH`.
Gate processes run under a controller-owned profile that makes the run
directory read-only, so a gate reads the frozen sensor it was given but cannot
write anywhere in the record it produces. Its own log files are opened by the
controller before the gate starts, so they are still written. The controller
recomputes every frozen sensor digest after the quick and full gates and rejects
the run if a snapshot changed. Both properties are language-agnostic: they hold
whatever the gate command runs.

The implementer receives a shallow checkout with no remote, so target repository
history is absent. It runs inside a controller-owned macOS sandbox profile that
denies reads and writes to source sensors, frozen sensors, run evidence, state
repository metadata, and the source repository's Git object store, and denies
every write to the source repository. The reviewer runs under the same profile
extended with a write denial covering the candidate worktree, so its read-only
nature is mechanical rather than instructed. The actuator receives only the
controller-selected gate output on the next iteration. CodeServo fails closed
where `sandbox-exec` is unavailable.

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

The target repository owns the desired operating envelope (`.codeservo/constitution.toml`). `TASK.md` supplies a temporary desired delta. The coding agent is only an actuator. Tests, linters, scope constraints and independent semantic review are sensors. CodeServo is the controller. Git is the state substrate. `evidence.json` is the audit record.

That is enough to test V0 without prematurely building a software factory.
