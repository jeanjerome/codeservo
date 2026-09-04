# Release Notes

What each version of the controller brought. The versions up to 0.6.0 were
proposed by a run of the one before them; see
[self-hosting](features/self-hosting.md).

### 0.8.0

The loop iterates on what a control said. A failing gate's observation reaches
the implementer before the raw output tail: its summary, its findings with the
path and line each names, its metrics. The prompt names every acceptance
criterion by its id and, from the second iteration on, carries one line per
iteration already spent. The full gates and the review no longer end a run: one
iteration chains scope, quick gates, full gates and review, and the first stage
that decides against the candidate writes the feedback of the next. Records
declare `schema_version` 17 and place the full gates and the review under the
iteration that measured them.

A criterion names what decides it. `{gate: <name>}` hands it to a gate the
constitution declares, `{review}` leaves it to the reviewer, and a task naming
a gate that is not declared is refused before anything is frozen. A criterion a
gate decided is not put to the reviewer, and a failing gate names the criteria
it leaves unsatisfied.

A gate that writes a document can be held to its own metrics. `ratchet = {
missing = "<=", line_coverage = ">=" }` is held between the document the gate
wrote at the baseline and the one it writes on the candidate, and a passing
gate that breaks a ratchet decides against the candidate as a failing one does.
A ratchet is refused on a gate that writes no document and on a gate outside
the baseline, either of which would be a control that can never speak.

A run closes on three issues rather than two. `ESCALATED` says every
deterministic control let the candidate through and what remains is a person's
to settle: a criterion nobody could verify, a review contradicting a gate that
passed, a budget spent on review objections alone. Nothing is fed back on that
path, because nothing there is the actuator's to correct. Records declare
`schema_version` 18.

An accepted run is landed by a person. `codeservo land` applies the patch to
the repository the run measured it against and appends `run.landed` after
`run.finished`, chained like every other event, so the record stays the
document the decision closed. The findings the review reported on the landed
candidate go to a register, one line each, with no gate named until one covers
them: a defect the review caught and no deterministic control did is a control
that is missing, and the register is where that is visible.

Every token a run consumes is counted and priced. One catalogue names every
model and effort a run may request, with dated list prices, and a model it does
not list — or lists for the other backend — is refused by name before the run
directory exists. Each actuation records what its backend reported consuming in
the five categories both count, the controller prices that from the catalogue
beside whatever cost the backend reported itself, and the run freezes the copy
that priced it next to the task and the constitution. Records declare
`schema_version` 19.

A second ecosystem. The execution provider sits behind a port of six
operations, with `pixi` and `mise` as its adapters, so a repository declares
the manifest and lockfile its own toolchain is pinned by and the controller
freezes, installs and measures through them. A Java repository under Maven is
measured the way a Python one is, and no tool of either ecosystem enters the
product.

Gates report through the formats their tools already write. JUnit XML, SARIF
and LCOV are read and projected onto the observation every reader already
knows, so the adapter a target repository supplies is reduced to running its
tool with a standard output option. A gate names its format and the pattern its
tool writes reports under, and only the files that measurement wrote are read.
One reading matters more than any count, and it is the same question in all
three: a tool that died in the middle writes the same empty set as a clean
tree, so a report saying it did not finish is a fault of measurement and never
a pass.

Confinement is portable. The profile and the mechanism are separated: one
module carries the profile and names no mechanism, a port answers whether this
host can apply one, runs a command under it and says whether the command really
ran under it, and `sandbox-exec` and Bubblewrap are the two adapters behind it.
A target repository declares its execution provider and never its confinement,
because a candidate able to name the mechanism holding it would be negotiating
its own cage.

`codeservo doctor` says what a host provides before a run finds out. A run
refuses where no mechanism can confine a process, where the agent CLI it names
is absent, or where the provider it would measure through is missing — and it
refuses at the point where it finds out, after a run directory exists. Every
reading is taken by asking rather than by inferring: the mechanism by applying
a profile, a tool by running it, a repository by reading what it declares.

A run that stops before any control has spoken says so. `run.aborted` names
what ended it — a control input that broke, a Git operation that failed, an
operator interrupting — and states no status, because none of the three issues
would be true of a measurement that did not happen.

### 0.7.2

The execution environment block asserts only what a measurement established. It
opened every record with a constant saying no execution provider was declared,
so a run refused while its control inputs are still being verified — refused
after its record exists and before the execution table is read — closed denying
a provider beside the digest of a constitution that declares one. The block now
opens with the provider the constitution declares, and `none` keeps meaning that
none was declared.

The candidate's `unchanged_at_end` carries no value until a recomputation
produces one. Whether the workspace still holds what was installed into it is
what taking the digests a second time answers, and the field was set when the
installation finished: every run that stopped before the first recomputation —
a verification run at a zero iteration budget among them — recorded a comparison
nobody made. The document says so in its type, so the block omits the verdict
rather than defaulting it.

### 0.7.1

The Git metadata of a measured tree is protected where Git writes it. The gates
measuring the source repository were held to `<repo>/.git`, which is that
metadata only in an ordinary checkout: in a linked worktree it is a file holding
a pointer, Git writes elsewhere, and a baseline gate could rewrite the refs, the
objects and the index of a repository it was only supposed to read. The
directory the controller already resolves, and already denies to the actuator,
now bounds those gates too.

The actuator's view of the frozen constitution names what each gate measures. It
rendered a gate's command alone, so a gate naming a provider task reached the
actuator as `command = null` — a declaration no constitution can make, and not a
TOML document either.

### 0.7.0

Gates report what they measured. A gate declaring `result_format =
"codeservo-json"` answers with a document beside its exit code, and the record
carries it: violations with their rule, file and line, type errors, test counts
and the cases that failed, the layer graph and every forbidden import, coverage
against its floor, fuzzing budgets and the coverage each boundary reached,
durations against their ceilings. Before this, the number a tool computed lived
in a log nothing compared.

The location that document goes to reaches a provider task as its argument.
`pixi run --clean-env` empties the environment a task starts with, so no
variable could carry it, and until now no gate could both run in a locked
environment and report what it measured.

Parsing boundaries are stated as properties and searched as bytes: the six
surfaces a document, a record or a stream arrives through, and a coverage-guided
fuzzer on the three another party supplies. Records declare `schema_version` 16.

### 0.6.0

A run records its own trajectory. `events.jsonl` sits beside the record: one
event per transition, chained by digests and flushed before the transition it
records becomes visible. `codeservo verify-run` decides about a run directory
from that directory alone, reporting `VALID`, `INVALID` or `INCOMPLETE`. A
record edited after the fact can no longer make a rejected run look accepted.
Records declare `schema_version` 14 and name their journal.

### 0.5.0

A repository can be measured through an environment its lockfile pins. A
constitution declares an `[execution]` provider, a gate names a task, and the
controller freezes the manifest and lockfile digests and the resolved inventory
before the baseline, then installs the environment into the isolated checkout
without resolving. Every gate is confined to the tree it measures, whose Git
metadata and provider directory become readable and not writable for gates and
actuator alike, and the candidate's state is compared across each measurement
phase, so a gate that changed the tree ends the run even when it exited zero.
Records declare `schema_version` 13 and carry one isolation document per
measured tree.

### 0.4.0

Inference becomes an explicit control input. `codeservo models` projects the
provider caches on this machine into an inventory; a run freezes a complete
backend, model, effort and speed profile and refuses a request that inventory
contradicts, before any checkout or agent process exists. The reviewer carries
its own backend and profile, so the two roles vary independently. The
development environment is locked with Pixi. Records declare `schema_version`
10.

### 0.3.0

The record declares what it is and who produced it: `schema_version` 8, the
CodeServo version and source commit, the actuator name and version, and the
Python and Git versions.

### 0.2.0

The reviewer receives the deterministic gate observations it cannot produce
itself — what each gate measured, carrying no filesystem path and no sensor
source. The controller's own test suite composes with gate and review isolation,
so the confinement is exercised rather than described.

### 0.1.0

The first frozen controller: baseline gates, an isolated shallow checkout, the
actuator, the scope sensor and quick gates, deterministic feedback, full gates,
an independent read-only semantic review, a mechanical decision, portable run
evidence, external acceptance sensors, and a configurable state directory.
