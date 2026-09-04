# Evidence, and What a Run Measures

A run answers from its directory alone. Nothing here is a log to be read
charitably: every artefact is named by a path and a digest, every transition is
an event chained to the one before it, and `verify-run` reaches one of three
verdicts about a directory it only reads.

## Where a run keeps what it produced

`--state-dir` selects where controller-owned sensors, evidence, and temporary
working trees are stored. It defaults to `~/.codeservo` and must be outside the
target repository. Sensor references in the constitution resolve below
`<state-dir>/sensors/`.

```text
<state-dir>/
├── sensors/<repo>/<sensor>/
├── worktrees/<repo>/<run-id>/
└── runs/<repo>/<run-id>/
    ├── TASK.md
    ├── constitution.toml
    ├── catalogue.toml           # the model prices this run was rated by
    ├── sensors/                 # frozen sensor snapshot
    ├── environment/             # packages.json, when a provider is declared
    ├── baseline/
    ├── iterations/
    │   ├── 01/
    │   │   ├── input.patch
    │   │   ├── prompt.md
    │   │   ├── agent/
    │   │   ├── actuator.patch
    │   │   ├── quick/
    │   │   ├── observed.patch
    │   │   ├── full/                   # once the quick gates passed
    │   │   ├── full.patch
    │   │   ├── review/                 # once the full gates passed
    │   │   └── controller-feedback.md  # when a measurement decided against the candidate
    │   └── ...
    ├── change.patch
    ├── events.jsonl
    └── evidence.json
```

The evidence directory is outside the target worktree so the actuator cannot
rewrite the controller's record.

## The record and the journal

`evidence.json` is the summary, checkpointed during the run and declaring its
own shape through `schema_version`. A gate that answered with a document has it
beside its logs, digested like every other artefact, so what a gate measured is
part of the record and not only part of a log. Each iteration records the exact feedback
received, the prompt digest, the repository state before and after actuation,
the state observed after the quick gates, and any feedback generated for the
next iteration. Paths are relative to the run directory, so a copied run remains
self-contained and verifies wherever it is moved. SHA-256 digests cover frozen
sensors, patch snapshots, prompts, feedback, gate outcomes and logs, agent
events and messages, and reviewer artefacts.

The record names the controller that produced it — CodeServo version and source
commit, both actuator names and versions, Python and Git versions — and, for
each role, the requested inference profile beside what the backend reported. A
Claude Code session records the model identifier it resolved to and the tokens
every model spent, because a model alias moves over time while two runs have to
stay comparable.

`events.jsonl` is the trajectory. One immutable event per transition, carrying a
sequence, a payload, the previous event's digest and its own; the digest covers
the canonical form of the event without that field, so the order is verifiable
and an edit anywhere breaks the chain from that point on. Each event reaches the
file system before the transition it records becomes visible, so a decision
never exists only in memory.

```text
run.started  inputs.frozen  inference.profiles_frozen
environment.validated  environment.prepared
gate.finished  baseline.finished  workspace.ready
actuator.started  actuator.finished  actuator.profile_observed
feedback.emitted  budget.exhausted
review.finished  review.profile_observed
decision.recorded  run.finished
```

Events appear where the run took the transition: a constitution declaring no
provider produces no environment event, and nothing claims one happened.

## Verifying one run

`codeservo verify-run <run-directory>` decides about a run directory from that
directory alone, and [commands](commands.md#verify-run) says what it checks and
what each verdict means.

## The findings register

An accepted run is landed by a person running `codeservo land`. The findings
the review reported on the landed candidate go to
`<state-dir>/findings/<repository>.tsv`, one line each, with `none` in the last
column until a gate covers the finding and a person writes its name there. A
defect the semantic review caught and no deterministic control did is a control
that is missing, and the register is where that is visible rather than
remembered. Each error is meant to strengthen the system, and this is the file
where that stops being a slogan.

## What a run consumed

Each actuation records what the backend reported it consumed, in the five
categories both backends count: uncached input, cache reads, cache writes,
output, and the reasoning part of the output. The two CLIs spell them
differently, and each adapter puts every count under the same name. The
controller rates them at the frozen catalogue's list prices, block by block,
and the record carries the rated cost beside whatever cost the backend itself
reported, never merged. Codex names no model, so its tokens are rated at the
requested one and the record says the attribution is the controller's. A block
the catalogue cannot rate, a model it does not list or a cache duration it has
no price for, leaves the cost unknown rather than understated. A list price is
a measure comparable across backends and runs, not an invoice.

## What is not measured

Not lines of code, not diff size, not throughput. Coding throughput is not a
measure of productivity, and a record carrying one would invite exactly the
optimisation this harness exists to prevent. What it carries is whether the
candidate was accepted on the first pass, how many iterations and review rounds
it took, what each phase cost in time and tokens, and — through the findings
register — what the gates did not catch.
