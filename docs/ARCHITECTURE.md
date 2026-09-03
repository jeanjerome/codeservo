# Controller Architecture

How the controller is built and what it guarantees. The rules for writing code
inside it are in [QUALITY.md](QUALITY.md); the commands that measure it are in
[COMMANDS.md](COMMANDS.md).

## Package Layers

`src/codeservo/` has one directory per layer, `tests/` mirrors it, and
`templates/` holds the published examples and schemas. Dependencies point
inward: `domain` holds the values every other layer is written in terms of,
`policies` reads the constitution, `runtime` runs and confines a process,
`workspace` owns the trees a run works on, `sensors` measure them, `actuators`
is the backend port and its two adapters, `evidence` is what a run leaves
behind, `controller` is the loop and its `phases`, `cli` is the command line,
and `resources` locates the documents the package publishes.

That direction exists twice. The paragraph above explains it; `.importlinter`
declares it as an ordered contract that the `architecture` gate reads, failing
on any import that climbs a layer, including one that climbs through a chain of
modules. The file is a protected path, so a candidate cannot loosen the rule to
pass the gate that measures it against.

## The Control Loop

The controller:

1. freezes the task, the constitution, the base Git SHA, and the implementation inference profile;
2. verifies baseline gates;
3. creates an isolated shallow Git checkout without a remote;
4. invokes the configured actuator CLI as the implementation actuator, with a prompt naming every acceptance criterion by its id, the actuator's view of the constitution, one line per iteration so far and the previous iteration's feedback in full;
5. runs scope and quick gates;
6. feeds failures back to the actuator: for each failing gate, the document the gate wrote when it wrote a valid one (summary, findings with the place each names, metrics), then the tail of what it printed;
7. iterates until quick gates pass or the budget is exhausted;
8. runs full gates;
9. hands an independent, read-only semantic reviewer an immutable summary of the gates that passed, carrying no filesystem path and no sensor source, and invokes it;
10. applies acceptance rules mechanically;
11. persists complete run evidence.

Gates are authoritative. Semantic review is a sensor, not the final authority. Controller-owned evidence must remain outside the actuator's write scope and must reconstruct the complete control trajectory.

## Inference Profiles and Actuator Confinement

The actuator is selected per run with `--actuator claude` or `--actuator codex`, defaulting to `$CODESERVO_ACTUATOR` and then to `claude`. Its inference profile is `--model`, `--effort` and `--speed`. The reviewer carries its own, `--review-actuator`, `--review-model`, `--review-effort` and `--review-speed`, so a Codex implementation can be decided by a Claude review or the reverse; `--review-actuator` defaults to the implementer's backend. Both profiles are checked against the locally projected provider inventory of their own backend before the isolated checkout is created: only an inventory that lists the model can contradict the request, and only about an effort or a speed it declares itself. A profile it cannot check is recorded `unverified` and proceeds, because an inventory is informative and never an authority on what an account may use. The record keeps the requested profile beside the observed one, never fills the second from the first, and states per field why the observed value is there or is not: `reported` when the backend's own output carried it, `not_reported` when it did not, a value being present exactly when its provenance says `reported`. What each backend actually reports was measured rather than assumed, and is written into protocol 032: Claude names a model and a speed and no reasoning effort at all, and the Codex event stream names none of the three. Both the implementer and the read-only reviewer run inside a controller-owned macOS seatbelt profile. macOS refuses to apply a seatbelt profile inside another one, so an actuator confined that way must not sandbox itself: Claude Code runs with its permission checks bypassed and Codex with `--sandbox danger-full-access`, and the controller profile is the only confinement in both cases.

## Execution Environment

A constitution may declare an execution provider in `[execution]`; a gate then names a provider task instead of a shell command, and the controller builds the command line, always naming the manifest of the tree that gate measures. Before the baseline it freezes the manifest and lockfile digests and the inventory the lockfile resolves to, and a lockfile that disagrees with its manifest ends the run before any checkout exists. After the isolated checkout is created and before the first actuation it installs that environment from the committed lockfile without resolving, never into the source repository, whose environment must already be there for a baseline task gate. Every gate process runs under `PIXI_OFFLINE`, `PIXI_NO_INSTALL` and `PIXI_FROZEN`, so a measurement can neither resolve nor install. A constitution that declares no provider keeps shell gates unchanged.

## Structured Gate Observations

A gate may declare `result_format = "codeservo-json"` beside its exit code. The
controller then creates a location it owns, outside the run directory and outside
the tree that gate measures, hands the gate that path and hands it to no other
process, validates what was written
against the six-field contract the package publishes at
`observation.schema.json`, and keeps that document byte for byte in the record. A
document that is absent, malformed or that contradicts the exit code is a fault
of the sensor and not a failure of the candidate: the run ends there, on that
classification alone, in whatever phase it happened, and nothing is fed back to
the actuator. The exit code stays the verdict; the document says what was seen.
The schema is published for adapters to read and is never executed — the six
fields are checked directly, and a test holds the published document and the
enforced contract to each other.

The location reaches the two kinds of gate by two channels, because only one
reaches each. A gate naming a shell command reads it from
`CODESERVO_OBSERVATION_PATH`. A gate naming a provider task cannot: the task
runs with a clean environment, so a variable set around the command does not
survive into it, and neither does one the manifest re-exports from it. Its
location is therefore appended to the command as the task's one argument,
which the provider passes through. Both are the controller's business: the
target repository writes an adapter that takes a location, not one that knows
where the location came from.

## Measurement Confinement

Gates run under the same mechanism with the run directory read-only, so no gate can write into the record it produces, and each gate is confined to the tree it measures: its Git metadata and its provider directory are readable and not writable, for the gate and for the actuator alike. Reading survives that confinement — `git status`, `diff`, `log`, `ls-files` and `rev-parse` all work, and a task gate runs on the environment it cannot write. What a profile cannot express is caught afterwards: the controller recomputes every frozen sensor digest and the workspace digests after the quick and full gates, and compares the candidate's state across each measurement phase, so a gate that changed the tree ends the run even when it exited zero. Prefer this kind of language-agnostic mechanical property over per-ecosystem settings: target repositories will not all be Python.

## Evidence and Verification

A run records its trajectory as it happens. `events.jsonl` sits beside the
record: one event per transition, carrying a sequence, a payload, the previous
event's digest and its own, each reaching the file system before the transition
it records becomes visible, so a decision never exists only in memory.
`codeservo verify-run <run-directory>` then decides about a run directory from
that directory alone, reporting `VALID`, `INVALID` or `INCOMPLETE` with exit
statuses 0, 1 and 2: it checks the frozen inputs, the sensors, the environment
inventory, every artefact the record names by a path and a digest, the digests a
record recomputes from itself, the chain, and the agreement between the last two
events and the recorded decision. A record edited after the fact cannot make a
rejected run look accepted, because the decision is itself an event the chain
closes over.

