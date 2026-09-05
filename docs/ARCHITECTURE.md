# Controller Architecture

How the controller is built and what it guarantees. The rules for writing code
inside it are in [QUALITY.md](QUALITY.md); the commands that measure it are in
[COMMANDS.md](COMMANDS.md).

## Package Layers

`src/codeservo/` has one directory per layer, `tests/` mirrors it, and
`templates/` holds the published examples and schemas. Dependencies point
inward: `domain` holds the values every other layer is written in terms of,
`policies` reads the constitution, `runtime` runs a process and confines it
through a port whose adapters are the host's own mechanisms,
`workspace` owns the trees a run works on, `sensors` measure them, `actuators`
is the backend port and its two adapters, `evidence` is what a run leaves
behind, `controller` is the loop and its `phases`, `cli` is the command line,
and `resources` locates the documents the package publishes.

That direction exists twice. The paragraph above explains it; `.importlinter`
declares it as an ordered contract that the `architecture` gate reads, failing
on any import that climbs a layer, including one that climbs through a chain of
modules. The file is a protected path, so a candidate cannot loosen the rule to
pass the gate that measures it against.

A directory is a layer because it names a different kind of thing, never
because it grew large, and a file inside one is its own module when it owns a
vocabulary, a contract or an adapter that the rest of the layer uses without
knowing what is behind it. Four packages are cut that way, and the cut is where
the direction above does its work.

`sensors` measures and never decides, and its four modules answer two
questions. What is measured: `scope.py` is the one measurement the controller
makes itself, reading Git against the frozen base commit for the files that
moved, the lines they moved by and the protected paths among them; `gates.py`
is every measurement delegated to the target repository's own tools, building
the command line, running it confined and keeping both streams with their
digests, its verdict derived from the exit code rather than chosen. What a
measurement may say: `observations.py` owns the contract of the second answer —
the six fields, the two vocabularies, the validation that names the field at
fault, and the classification of a document against the exit code — and it
interprets no tool; `reports.py` owns the mechanism that finds the report
files a measurement wrote, and one module per format reads them and projects
them onto that contract, `junit.py` for test results, `sarif.py` for analysis
results and `lcov.py` for coverage tracefiles. A document therefore reaches the
record by two channels in one shape, written by the gate's own adapter or
projected here from what its tool wrote, and every reader downstream — the
feedback, the ratchets, the bundle the reviewer is handed, the record itself —
knows the shape alone. A fourth format is a module beside those three and moves
none of them.

`actuators` and `workspace` have the same shape twice, a port and its adapters:
`base.py` is the backend port and `claude_code.py` and `codex.py` answer it,
`provider.py` is the execution provider port and `pixi.py` and `mise.py` answer
it. The layer above imports the port and never an adapter, and every fact
measured about one tool lives in that tool's file, which is what lets a second
ecosystem arrive without the controller learning its name. `actuators` and
`sensors` are siblings that may not import each other, so what proposes a
change never sees what measures it.

`controller` is the loop and `controller/phases/` its steps, each handed the
frozen context and the open record, measuring, writing what it measured, and
either letting the run continue or raising the rejection that ends it. None of
them closes a run: `run.py` does that in one place, whichever step ended it, so
a decision can never be reached without being written. The modules beside the
phases are what a phase is written in terms of — the frozen context, the record
and the document it holds, the confinement profiles, and the policies applied
to what was measured. The `coverage` gate names the decision core among them
rather than the package: the constitution reader, the convergence decision, the
ratchet and the record verification, which are where a wrong answer is a wrong
verdict, and where a well covered periphery would otherwise carry a total that
hides them.

The word *sensor* names two things that are not the same. This package is the
code that measures, and it ships in the wheel. An external acceptance sensor is
a protocol instrument, written and frozen before the actuation it constrains,
kept in the state repository where no actuator can read it, and executed by the
one gate that declares it. Both report and neither decides, which is why they
share the word.

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

The actuator is selected per run with `--actuator claude` or `--actuator codex`, defaulting to `$CODESERVO_ACTUATOR` and then to `claude`. Its inference profile is `--model` and `--effort`, both named by the caller: the model is the complete identifier of one the catalogue lists for that backend, the effort one of `low`, `medium`, `high` and `xhigh`, handed to the CLI unchanged. The reviewer carries its own, `--review-actuator`, `--review-model` and `--review-effort`, defaulting to the implementer's, so a Codex implementation can be decided by a Claude review or the reverse. The catalogue is a document this package publishes, `models.toml`, and the only source of what a run may name: no provider cache is read and no account is asked, a model the catalogue does not list or lists for the other backend is refused by name before the run directory exists, and whether a model accepts an effort is the CLI's to decide. The record keeps the requested profile beside the observed one, never fills the second from the first, and states per field why the observed value is there or is not: `reported` when the backend's own output carried it, `not_reported` when it did not, a value being present exactly when its provenance says `reported`. What each backend actually reports was measured rather than assumed: Claude names the model it ran on and no reasoning effort, and the Codex event stream names neither. Both the implementer and the read-only reviewer run inside a controller-owned profile, applied by whichever mechanism the host carries. A confined process has one confinement authority and it is that profile, so an actuator under it must not sandbox itself: Claude Code runs with its permission checks bypassed and Codex with `--sandbox danger-full-access`. The rule was first forced by macOS, which refuses to apply a seatbelt profile inside another one, and it holds on either host; the record names the mechanism that was applied, or Codex's own `workspace-write` when the controller applied none.

## Consumption and Cost

Each actuation records what the backend reported it consumed, under the model it billed, in the five categories both backends count: uncached input, cache reads, cache writes, output, and the reasoning part of the output. The two CLIs spell them differently, and each adapter puts every count under the same name: Codex reports a total input with the cached and written parts inside it, Claude reports the three apart, and both are measured rather than assumed. The adapter never prices: the price is a policy over that measurement, applied by the controller with the catalogue the run froze beside its task and constitution, so a Codex run and a Claude run are comparable on one arithmetic. Each billed block is rated at the model the backend named, or at the requested model when it named none, and the record says which. The rated cost sits beside the cost the backend itself reported, never merged with it. A block the catalogue cannot rate leaves the cost of the block and of the whole unknown rather than understated: a model it does not list, a cache-write duration its price table has no line for, or a count the stream did not carry. The durations of a run are read off the record and the journal, which already carry them per gate, per actuation and per event, so no field was added for them.

## Execution Environment

A constitution may declare an execution provider in `[execution]`; a gate then names a provider task instead of a shell command, and the controller builds the command line, always naming the manifest of the tree that gate measures. The provider is a port of six operations in `workspace/provider.py` — freeze, install, the task command, the measurement environment, the provider directory and the configuration file — and two adapters answer it, `workspace/pixi.py` and `workspace/mise.py`. The controller reads the port alone; everything a provider does, and every fact measured about it, lives in its adapter, and the constitution reader refuses a provider no adapter answers for. Before the baseline the controller freezes the manifest and lockfile digests and the inventory the lockfile resolves to, and a lockfile that disagrees with its manifest ends the run before any checkout exists. Where the tools are installed is the one thing the port lets an adapter decide. Pixi keeps them in the tree it measures, so the environment is installed into the isolated checkout after it is created and before the first actuation, never into the source repository, whose environment must already be there for a baseline task gate. mise keeps them outside every tree, so the controller installs them once into its own directory, `<state-dir>/providers/mise/`, before the baseline, and both trees measure through it; the candidate's provider files are digested once the checkout exists. Every gate process runs under the variables the adapter names, which forbid the provider to resolve or install: `PIXI_OFFLINE`, `PIXI_NO_INSTALL` and `PIXI_FROZEN`; `MISE_OFFLINE`, `MISE_LOCKED`, the four auto-install settings, the one manifest read by name, the search for configuration stopped above it and the operator's own files replaced by an empty one. A pixi task starts with an environment the provider cleans; a mise task inherits the environment it is started from, so the location of a frozen sensor reaches it and a sensor gate may name a task. A constitution that declares no provider keeps shell gates unchanged.

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
which the provider passes through. How far it passes it is the provider's
answer and not the same one: pixi hands it to the task's command, while mise
appends it as text to the end of the task's script, so under mise the location
reaches an adapter only when the task is one line naming it. Both are the
controller's business: the target repository writes an adapter that takes a
location, not one that knows where the location came from.

A gate may instead declare a format the controller projects, `junit-xml` for
test results, `sarif` for analysis results or `lcov` for coverage tracefiles,
and name in `reports` the pattern under which its tool writes them, relative to
the tree it measures. The gate is
handed no location. `sensors/reports.py` lists the matching files before the
gate runs and after it, and hands the module of that format the ones this
measurement wrote — new, or whose size or write time moved — which projects them
onto the same six-field document; it is then held to the same contract and kept
the same way, so every reader of an observation, feedback, ratchets and review,
is unchanged. A report the gate left as it found it is not this measurement's
and is not read; nothing is deleted from the tree to make that so. The status of
a projection is the verdict the exit code reached, because the controller wrote
it and contradicts nothing it decided; a report the reader cannot make sense of
is `invalid`, and a gate that passed and wrote no report is `absent`, both
faults of the sensor. No tool's name enters the controller: the pattern names
the location, and the location is the target repository's, which must also
ignore it in Git — a baseline gate leaving a tracked report behind has changed
the tree it was only measuring, and the run ends there.

A ratchet is what turns these counts into a control. It reads the metrics of
the document the gate wrote at the baseline and of the one it wrote about the
candidate, so `line_coverage = ">="` over an LCOV gate holds coverage without
the target repository writing anything, and `branches = ">="` beside it refuses
a candidate that stopped instrumenting them. That is why a family the tool did
not measure is still counted, at zero, rather than left out of the document.

Each format is read as its own specification defines it, and what a real
producer writes was measured rather than assumed. `junit-xml` is a `testsuite`
of `testcase` elements, alone or under `testsuites`, as Surefire, Gradle,
pytest and Jest all write it; a `file` and `line` a case names are kept when
they point inside the tree. `sarif` is read at version 2.1.0, and another
version is refused rather than guessed at: a result takes its level from
itself, then from its rule's default configuration, then from the default the
specification defines; a result reporting something other than a failure is not
counted, and one the tool suppressed is counted apart from the findings, so a
candidate hiding a result cannot hide the count of what it hid. Arrays deciding
how many results there are must hold results, because reading past one that
does not would report a count no measurement produced. `lcov` is the tracefile
of `SF:` records `lcov` defined, as coverage.py, Jest, c8, nyc and gcovr all
write it; the counts are the `DA:`, `BRDA:` and `FN`/`FNDA:` records and never
the summary lines a producer may write beside them, which were measured to
reproduce those records exactly. What one file is named twice is counted once,
because LCOV merges by summing what each record says of a line, a branch or a
function, so two records or two tracefiles covering one file are one file.

Each of the three has one reading that matters more than its counts, and it is
always the same question: did the tool finish? A tool that died halfway writes
the same empty result set as a clean tree. SARIF says so in
`invocations[].executionSuccessful`, an LCOV tracefile says it by ending inside
a record, and a report saying either is a fault of the measurement and never a
green.

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

## Confinement

A profile states what a process may not reach and what it may only read. Which mechanism enforces it belongs to the host, so the two are separate: `runtime/sandbox.py` holds the profile and names no mechanism, `runtime/confinement.py` is the port, and `runtime/seatbelt.py` and `runtime/bubblewrap.py` are the two adapters. A target repository declares its execution provider and never its confinement, because a candidate able to name the mechanism holding it would be negotiating its own cage. The mechanism is established by applying a profile rather than inferred from the platform name — a stock Ubuntu 24.04 restricts unprivileged user namespaces through AppArmor and installs no profile for `bwrap`, which then fails setting up its uid map — and a host with no mechanism refuses the run instead of executing it unconfined.

The two mechanisms agree on what a profile means and on nothing about how to say it, so what each adapter does was measured rather than read off a manual. macOS denies over an allowed default. bubblewrap builds up what a process can see, so the same profile becomes a transparent bind of the whole filesystem and then the rules that take things back: a later rule wins, so read-only paths are emitted before denials; a denial is an empty directory bound read-only rather than a tmpfs, which takes the write and reports a success; a denied file is the null device, because a directory cannot be bound over a file. Nothing binds over a path that is not there, since bubblewrap creates a missing mount point on the real filesystem and binding over an absent `.git` would leave an empty one behind — the confinement would have changed the directory it exists to leave alone. A read-only path that is missing is a fault named by its path, because the run works through it; a denied path that is missing holds nothing to deny.

Neither mechanism reports a profile it failed to apply through the exit code, because that code belongs to the measured command: a mechanism that could not start the command and a gate that legitimately failed end on the same number. So each adapter answers separately whether the command ran under the profile at all — `sandbox-exec` exits `EX_DATAERR` on a profile it cannot parse and `EX_OSERR` on an executable it cannot start, writing its own report on stderr and never reaching the command; bubblewrap's `--json-status-fd` carries an exit code only once the command has run, where `--info-fd` writes the child's pid before the mounts are applied and establishes nothing. A command that did not run stops the run rather than returning a verdict about the tree. A process that timed out is not confirmed: it leaves the same silence behind and the run already knows which one it is.

`tests/runtime/test_confinement_conformance.py` states the contract once and runs it against whichever adapter the host answers with. Every denial there sits beside a reading that must succeed under the same profile, because a refusal is evidence only when the profile also permits something: a profile that was never applied refuses everything for the wrong reason and reads exactly like a confinement that works.

## Measurement Confinement

Gates run under the host's mechanism with the run directory read-only, so no gate can write into the record it produces, and each gate is confined to the tree it measures: its Git metadata and the provider directory it measures through — `.pixi` inside the tree, or the controller's `<state-dir>/providers/mise/`, one directory for both trees — are readable and not writable, for the gate and for the actuator alike. Reading survives that confinement — `git status`, `diff`, `log`, `ls-files` and `rev-parse` all work, and a task gate runs on the environment it cannot write. What a profile cannot express is caught afterwards: the controller recomputes every frozen sensor digest and the workspace digests after the quick and full gates, and compares the candidate's state across each measurement phase, so a gate that changed the tree ends the run even when it exited zero. Prefer this kind of language-agnostic mechanical property over per-ecosystem settings: target repositories will not all be Python.

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

