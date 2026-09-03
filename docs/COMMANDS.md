# Build, Test, and Development Commands

How the controller is measured. Every command below runs from this
repository's root. The rules those measurements enforce are in
[QUALITY.md](QUALITY.md).

Always run the relevant tests after modifying CodeServo. Never weaken a test,
gate, invariant, or architecture check to make an experiment pass.

## Reference Validation

For the controller, the reference validation is the locked Pixi environment,
and it is the same eight measurements the repository's own gates name:

```bash
pixi lock --check --no-config
pixi run --locked --no-config lint          # ruff, on src, tests and tools
pixi run --locked --no-config types         # mypy, on the shipped tree
pixi run --locked --no-config test          # the suite
pixi run --locked --no-config compile       # byte compilation
pixi run --locked --no-config architecture  # the layer contract of .importlinter
pixi run --locked --no-config coverage      # the suite again, over the decision core
pixi run --locked --no-config fuzz          # the boundaries another party supplies
pixi run --locked --no-config duration      # what the loop repeats, under a ceiling
```

None of the eight writes into the tree it measures: `ruff` and `lint-imports`
run with their caches disabled, `mypy` sends its cache to `/dev/null`,
`coverage` declares a data file under the temporary directory, and the fuzz
driver keeps the fuzzer's corpus and its crashing inputs there too — so a gate
cannot change the candidate it is only observing.

`coverage` reports on the decision core alone — the acceptance rules, the
constitution reader and run verification — and fails under 94 percent. The
package as a whole measures higher, but a total over the package would let a
well covered periphery answer for the code that decides.

## What the Gates Report

Every one of the eight answers twice: an exit code, which stays the verdict,
and a document saying what it measured. Without the second one a run records
that a gate passed and nothing about what it found — the number the tool
computed lives in a log nobody compares, and two runs cannot be set beside
each other.

| Gate | What its document carries |
| --- | --- |
| `lint` | violations, and one finding per violation with its rule, file and line |
| `types` | errors, warnings, files checked, and one finding per diagnostic |
| `test` | tests, failures, errors, skipped, and one finding per failing case |
| `compile` | files submitted to the byte compiler |
| `architecture` | contracts kept and broken, files and dependencies in the graph, and one finding per forbidden import |
| `coverage` | the percentage, the statement counts, the floor, and one finding per file under it |
| `fuzz` | runs and coverage per boundary, and one finding per boundary that crashed or measured nothing |
| `duration` | each reading and its ceiling, and one finding per subject over one |

`tools/gate.py` holds the six projections over external tools, `tools/fuzz.py`
and `tools/duration.py` write their own, and `tools/observation.py` owns the
writing for all of them. Each is the deterministic adapter the architecture
describes: the tool keeps its own output format and the adapter projects it
onto the six fields the controller reads, so the controller learns nothing
about `ruff`, `mypy` or `coverage`.

Two properties hold across all eight and matter more than any projection. The
tool's own output goes through untouched, on the stream it was written to,
because that is what the controller feeds back to the actuator when a gate
fails: a wrapper that summarised it would quietly replace the feedback loop's
input with its own prose. And the exit code is the tool's — nothing in an
adapter decides a verdict.

Run by hand there is no location to write to and no document is written: an
adapter is given one, it does not choose one.

`pixi.lock` is a control input. It is committed, and it is never updated
implicitly while measuring; `--locked` fails rather than resolving, and
`--no-config` keeps user and system Pixi configuration out of the run. That
`--check` above is a maintainer command and must never run inside a run: on a
workspace whose lockfile and manifest disagree it exits 1 and rewrites
`pixi.lock`, mutating the very control input it reports as stale. A run gets the
same verdict, and the package inventory with it, from `pixi list --json --locked
--no-install --no-config`, which writes nothing. The
workspace declares `osx-arm64` and `osx-64` only, because `sandbox-exec` is the
isolation mechanism and the project does not advertise a platform it cannot
confine.

## Quick Loop

Running the suite on the host interpreter stays available for a quick loop. It
must name the tree it measures, so an ambient editable install cannot answer
for the checkout:

```bash
PYTHONPATH="$PWD/src" python3.12 -m unittest discover -s tests -v
```

## Record Parity

A structural change must leave `evidence.json` alone, and that is checked
rather than asserted. `tools/record_parity.py` drives seven trajectories — an
accepted run, one converging on the second attempt, an exhausted budget, a
review that objects until the budget is spent, one corrected after a review,
one escalated on a criterion the reviewer could not verify, and one measuring
through a provider — and captures the
shape of each record, its event sequence, the artefacts the run directory
holds and the `verify-run` verdict, with everything the clock, the host or a
temporary location decides masked out. Two captures taken from two
interpreters agree byte for byte, which is what lets a reference be committed.

`tools/record_parity.reference.json` is that reference and is a control input:
the workflow compares every commit against it, so a change that moves the
record moves the reference in the same commit, where the diff says what moved.

```bash
pixi run --locked --no-config python tools/record_parity.py capture /tmp/head.json
pixi run --locked --no-config python tools/record_parity.py compare \
  tools/record_parity.reference.json /tmp/head.json
```

Regenerate the reference only when the record was meant to change, and say in
the commit what moved:

```bash
pixi run --locked --no-config python tools/record_parity.py capture \
  tools/record_parity.reference.json
```

Capturing before and after a change remains available for a local loop, and
answers the same question without a commit in between.

## Continuous Integration

`.github/workflows/checks.yml` measures a maintainer's commit, which nothing
did before it: a documentation change, a generation checkpoint or a
constitution change was verified only at the baseline of the next run, so
detection was deferred and it was the run that failed rather than the commit.

It runs the reference validation above and the record comparison, on
`macos-15`. The gates are named twice, in the constitution and in the
workflow, so a step holds the two sets to being equal. Two jobs stand apart
and also run weekly, because they are the checks that can go red without this
repository moving: the dependency audit, and the longer fuzz search.

Only `osx-arm64` is exercised. The lockfile check resolves `osx-64` and
nothing runs it.

## Fuzzing

A gate, and a maintainer command that searches further:

```bash
pixi run --locked --no-config fuzz     # the gate: a fixed budget, a fixed seed
python tools/fuzz.py --search 600      # a longer search, per boundary
```

A property states a rule over every input of a shape someone described. A
fuzzer looks for the input nobody described: it mutates bytes, watches which
branches they reach, and keeps what reached a new one. The three boundaries it
drives are the ones another party supplies — the constitution, a run directory
handed to `verify-run`, and what the execution provider prints about itself.

The failure signal needs no oracle. A constitution is read or refused by name;
a record reaches `VALID`, `INVALID` or `INCOMPLETE`, or is refused; a
description gives four facts or a refusal. An interpreter traceback is none of
those, and it ends a run with the actuation already applied and nothing
recorded about it.

Three things make it a gate rather than a search. The seed and the run count
are fixed, so the same tree answers the same way twice. Crashing inputs and the
working corpus go under the temporary directory, where libFuzzer would
otherwise drop them into the tree being measured. And the coverage each run
gained is read back from the fuzzer's own last line, because a target that
instrumented nothing prints that line without a `cov:` field and still exits
zero after every requested run — a green measuring nothing.

The number beside each boundary is reported and never compared: it moves by a
few edges between two runs on one tree, so it says how much of the boundary
the search saw and not whether the tree changed. What does not move is the
verdict.

Random bytes never reach a boundary that sits behind a decoder, so the two
that take a document start from committed seeds in `tools/fuzz_corpus/`,
copied where the fuzzer may write. An input found to crash becomes a named
case in the suite rather than a file there: the suite runs everywhere, and a
corpus entry proves nothing about what it once caught.

## Duration Ceilings

```bash
pixi run --locked --no-config duration
```

Two durations decide what a run costs. The suite runs at every iteration of
the quick phase, and `verify-run` is what an auditor runs over a record
afterwards. Three readings are taken over them: the suite's total, its slowest
single case, and the median of several verifications of one run directory.

The total is the loosest on purpose — it grows whenever tests are added, which
is what should happen, so it is set to catch a change of order rather than a
change of percent. The slowest case is the reading that names a regression,
because it does not move when the suite grows.

Every ceiling is a wall clock, which is the noisiest sensor here: it measures
the machine as much as the tree. So each reading is printed beside the one
this tree was read at, whether or not it held, and the ceilings carry the
difference between the fastest machine that runs them and the slowest. A suite
that does not pass is refused rather than timed: what it took would be a
duration over a different amount of work.

## Mutation Testing

A maintainer command, and one that can never be a gate:

```bash
pixi run -e mutation --locked --no-config mutation
```

Coverage says a line ran; this says whether anything would have noticed had
the line been wrong. It changes one operator, constant or comparison at a time
in the four places where a silent change would cost the most — the acceptance
rules, run verification, the event chain and the scope control — runs the
tests covering that module, and counts what survived.

It edits the source file to make each mutant, so it modifies the tree it
measures, which is what keeps it out of the constitution. It asks Git whether
the tree is clean before it starts and after it finishes, because an
interrupted run leaves a mutant behind. It takes about twelve minutes, and the
workflow runs it weekly rather than on every commit.

The ceilings in `tools/mutation.py` are read off this tree. Three of them are
where the suite happens to stand rather than where it should: they stop those
modules getting quieter, and closing them is work of its own.

## Dependency Audit

A maintainer command, deliberately not a gate:

```bash
pixi run -e audit --locked --no-config audit
```

Three reasons keep it out of the constitution. It reaches a remote advisory
service, so its verdict follows that service and the network as well as the
tree, where a gate reads a tree and returns an exit code. Nothing a candidate
may do would fix what it reports, dependencies being a maintainer's to change,
so a baseline gate would block a run for a defect no actuation could correct.
And it pulls an HTTP client and twenty-eight packages with it, which is the
last thing the environment a confined measurement runs in should hold — hence
its own environment, solved apart, leaving `default` pinning the same
forty-six packages it did before.

It audits the Python distributions installed in the default environment, and
that is narrower than the environment: the native libraries, `openssl` and
`libcurl` and `git` among them, are outside its reach. The task names the path
it audits and checks it first, because `pip-audit` reports no known
vulnerabilities for a path that does not exist.
