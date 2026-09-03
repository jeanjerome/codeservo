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
4. invokes the configured actuator CLI as the implementation actuator, with a prompt naming every acceptance criterion by its id and by the control that decides it, the actuator's view of the constitution, one line per iteration so far and the previous iteration's feedback in full;
5. runs the scope sensor and the quick gates, then, once they pass, the full gates, holding each gate that declares a ratchet to the document it wrote at the baseline, then, once they pass, hands an independent, read-only semantic reviewer an immutable summary of the gates that passed, carrying no filesystem path and no sensor source, and invokes it about the criteria the task did not hand to a gate;
6. applies the acceptance rules mechanically to what the reviewer returned;
7. feeds the first measurement that decided against the candidate back to the actuator: for a failing gate, the acceptance criteria that gate decides, the document the gate wrote when it wrote a valid one (summary, findings with the place each names, metrics), then the tail of what it printed; for a ratchet a passing gate broke, the metric, both values and the direction; for the review, the criteria it did not find satisfied and the findings the constitution declares blocking;
8. iterates until an iteration is accepted, the budget is exhausted, or the review leaves open what no control can settle;
9. persists complete run evidence.

Gates are authoritative. Semantic review is a sensor, not the final authority. Controller-owned evidence must remain outside the actuator's write scope and must reconstruct the complete control trajectory.

The three measurements of an iteration are ordered by cost, and each opens
another iteration when it decides against the candidate; nothing distinguishes
a full gate from a quick one except when it runs. A reviewer is told nothing of
the iterations before the one it reviews, its own earlier answers included, so a
finding that does not recur is a measurement and not a concession. What ends a
run before the budget is a control error: a gate that changed the tree it
measured, a sensor that could not say what it saw, a reviewer that misreported
the criteria it was asked to decide. The record keeps every measurement under
the iteration whose candidate it measured, so the trajectory of a run that was
reviewed three times holds three reviews.

A run closes on one of three outcomes, and which one follows from what decided
against the candidate last. `ACCEPTED` needs every control. `REJECTED` is a
deterministic control's word: the scope, a gate, a ratchet, a frozen sensor
that moved, a sensor or a reviewer that could not say what it saw, or a budget
whose last iteration one of those refused. `ESCALATED` is the outcome when
every deterministic control let the candidate through and the review alone
left it undecided: a criterion the task left to the review that the reviewer
could not verify, with nothing else to correct; a criterion a gate passed that
the reviewer reports as not satisfied; or a budget spent on review objections
alone. The review is a sensor and not the final authority, so what it alone
holds against a candidate is a person's to settle rather than a rejection, and
what nobody could verify is a fact about the task before it is one about the
change. Nothing is fed back on the way to an escalation: none of it is the
candidate's to correct. A criterion the reviewer could not verify beside one it
found unsatisfied is fed back with it, because a candidate still to be
corrected is measured again.

## What Decides a Criterion

An acceptance criterion names the control that answers it: `{gate: unit}` hands
it to a gate the constitution declares, `{review}` leaves it to the review
sensor, and a criterion naming neither is reviewed. The task and the
constitution are held against each other before either is frozen, so a
criterion naming a gate no constitution declares ends the run there rather than
reaching a review that was never asked about it.

A criterion naming a gate is decided by that measurement, and the run reaches
the review only once every gate has passed. The reviewer is asked for the
others alone and told which criteria a gate settled; what it volunteers about
one of those is kept in the record and decides nothing, a gate being
authoritative and a review contradicting a green one being a disagreement
between two sensors. A failing gate is fed back and recorded with the criteria
it leaves unsatisfied, so a decision names the acceptance criterion a run
stopped on and not only the measurement that stopped it.

## Inference Profiles and Actuator Confinement

The actuator is selected per run with `--actuator claude` or `--actuator codex`, defaulting to `$CODESERVO_ACTUATOR` and then to `claude`. Its inference profile is `--model` and `--effort`, both named by the caller: the model is the complete identifier of one the catalogue lists for that backend, the effort one of `low`, `medium`, `high` and `xhigh`, handed to the CLI unchanged. The reviewer carries its own, `--review-actuator`, `--review-model` and `--review-effort`, defaulting to the implementer's, so a Codex implementation can be decided by a Claude review or the reverse. The catalogue is a document this package publishes, `models.toml`, and the only source of what a run may name: no provider cache is read and no account is asked, a model the catalogue does not list or lists for the other backend is refused by name before the run directory exists, and whether a model accepts an effort is the CLI's to decide. The record keeps the requested profile beside the observed one, never fills the second from the first, and states per field why the observed value is there or is not: `reported` when the backend's own output carried it, `not_reported` when it did not, a value being present exactly when its provenance says `reported`. What each backend actually reports was measured rather than assumed: Claude names the model it ran on and no reasoning effort, and the Codex event stream names neither. Both the implementer and the read-only reviewer run inside a controller-owned macOS seatbelt profile. macOS refuses to apply a seatbelt profile inside another one, so an actuator confined that way must not sandbox itself: Claude Code runs with its permission checks bypassed and Codex with `--sandbox danger-full-access`, and the controller profile is the only confinement in both cases.

## Consumption and Cost

Each actuation records what the backend reported it consumed, under the model it billed, in the five categories both backends count: uncached input, cache reads, cache writes, output, and the reasoning part of the output. The two CLIs spell them differently, and each adapter puts every count under the same name: Codex reports a total input with the cached and written parts inside it, Claude reports the three apart, and both are measured rather than assumed. The adapter never prices: the price is a policy over that measurement, applied by the controller with the catalogue the run froze beside its task and constitution, so a Codex run and a Claude run are comparable on one arithmetic. Each billed block is rated at the model the backend named, or at the requested model when it named none, and the record says which. The rated cost sits beside the cost the backend itself reported, never merged with it. A block the catalogue cannot rate leaves the cost of the block and of the whole unknown rather than understated: a model it does not list, a cache-write duration its price table has no line for, or a count the stream did not carry. The durations of a run are read off the record and the journal, which already carry them per gate, per actuation and per event, so no field was added for them.

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

## Ratchets

A gate reporting through a document may declare `ratchet = { missing = "<=",
line_coverage = ">=" }`: each named metric of its document is held to that
direction between the baseline and the candidate. The controller applies the
rule because it is a policy over two observations it already owns, the one the
gate wrote about the source tree before any candidate existed and the one it
wrote about the candidate, so an adapter is asked for nothing beyond the metric
it already reports. The exit code stays the gate's verdict, and a ratchet is
read over a gate that passed: a failing one has decided already, over a
different amount of work.

A broken ratchet decides against the candidate the way a failing gate does, in
the phase its gate belongs to: the iteration is not converged, the reasons name
the gate, the metric and both values, and the feedback hands the actuator the
same, as does the one line per iteration the next prompt carries. A ratchet is
silent when either document lacks the metric, a comparison with a value nobody
measured being a verdict no measurement produced; that silence is safe only
while the adapter writing the metric is a protected path, which the
constitution declares. The reader refuses a ratchet on a gate answering with its
exit code alone and on a gate outside the baseline, each being a control that
could never speak. The record gains no field: the two documents and the reasons
it already holds are what the comparison is recomputed from.

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

## Landing

`codeservo land <run-directory>` applies an accepted run's `change.patch` to
the repository the run measured and commits it there. It refuses everything
that would make the commit a change nothing measured: a run directory that
does not verify, a run that was not accepted, a run already landed, a
repository whose head is no longer the base commit the run froze, or one
holding uncommitted work. The commit's body names the run, the base commit
and the patch digest, so a commit can be dated against the record that
accepted it without opening the record.

The record is closed by the decision and the journal chains on it, so the
integration is not written into the record. It is one more event, `run.landed`,
appended after `run.finished` and chained like every other, naming the commit,
the base and the patch digest. The verification reads the journal in two
parts: the record's `events` block describes the journal as the decision
closed it, through `run.finished`, and exactly one event may follow, of that
type, on an accepted run, naming the base and the patch the record names.
Anything else after the decision is invalid, and a landing altered afterwards
breaks the chain like any other line.

The findings the review reported on the candidate that landed go to
`<state-dir>/findings/<repository>.tsv`, one tabulated line each: when, which
run, which commit, the severity, the place, the message, the evidence, and a
last column the controller writes as `none`. A person names there the
deterministic control that covers the finding once one does, which is what
makes the same kind of finding countable across runs and a repeat of it the
case for a gate.

