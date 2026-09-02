# Running One Controlled Change

The order of operations around `codeservo run`. The [README](../README.md) says
what each piece is — the constitution, the task, the state directory, the
record; this says when each is prepared, what must be true when it is, and what
to do with what the run returns.

One rule governs the whole order: **a control input is versioned before the
actuation it constrains.** A contract written after the change it judges cannot
be told apart from a contract written to fit it, and nothing in the record can
recover the difference later.

## Before the run

### 1. The source repository is clean

CodeServo refuses to start from a dirty tree, and the refusal is the point:
whatever is uncommitted is not part of what the run measured, and the record
names a base commit that would not describe the tree.

### 2. The constitution declares the gate that will run the sensor, and is committed first

That commit is the run's base. A sensor gate carries `baseline = false` and a
`sensor` reference; the controller resolves the reference under
`<state-dir>/sensors/`, freezes a copy into the run directory, and hands the
gate its location.

Which kind of gate can receive that location is not a preference:

- a gate that names a **shell command** keeps its environment, so it reads both
  the frozen sensor's location and the document location from it;
- a gate that names a **provider task** starts with an environment the provider
  cleans, so no variable survives into it. It is handed the document location as
  the task's one argument, and it cannot be given a sensor location at all.

A sensor gate therefore names a command.

### 3. Write the external sensor

It runs against the candidate, from the candidate's own working directory, and
it is the only party that decides whether the behaviour arrived. Give it:

- the **base commit**, pinned in the sensor itself, so the file set it computes
  is computed against a commit and not against whatever is checked out;
- the **files the task allows** and the **files it requires to move**;
- one contract per acceptance criterion, asserted at the boundary the task
  describes rather than through a private helper — what the sensor pins should
  be what a caller receives;
- **more than one instance of any rule the task states generally.** A frozen
  contract is what the loop actually enforces, so a rule with one pinned
  instance is a rule about that instance, and the rest is decided by review
  alone.

Keep out of it whatever a repository gate already measures. A sensor that runs
the repository's own suite puts itself between that suite and the streams the
controller handed the gate, and a suite that checks its own confinement stops
being able to tell.

### 4. Run the sensor against the base, and read every failure

Every failure must be an assertion naming the capability that is missing. A
failure that is a traceback of the sensor's own harness — an import error, a
fixture that does not build, a path that does not exist — is a defect of the
sensor, and a run started on it spends an actuator iteration on a message no
change can answer.

### 5. Replay the sensor the way a gate runs it

A run by hand takes a branch a gate never takes. Under a gate the sensor is
confined, the run directory holding the frozen copy is write-protected, and the
current directory is a checkout rather than the repository you are sitting in.
Replay it under those three conditions before freezing it.

What this catches and a direct run cannot: anything the sensor writes beside
itself — bytecode, a cache, a report — lands inside the directory a gate may not
write into, and code paths that only exist under confinement are never taken in
a terminal.

### 6. Show that the sensor can also pass

Apply the change yourself in a throwaway checkout and run the same confined
gate. A sensor nobody has seen accept is a sensor that measures nothing, and the
difference between thirteen failures and fifteen passes is the only evidence
that it discriminates.

Then destroy that checkout. The actuator produces the change; you produced the
contract.

### 7. Write the task

Its criteria are what the review answers one by one, so each is a statement
about observable repository state. Before freezing it, check that it carries:

- **its allowed file set, in the task itself** and not only in the sensor — and
  that the set contains every module its criteria need;
- **a slot for every value a criterion demands**, when it freezes the shape of a
  document;
- **every external fact verified by running the tool**, not read about. A flag,
  a configuration key, an output format, the value at a boundary rather than in
  the middle. Write the observed value into the task. An invented provider key
  reaches a green gate set and is caught, if at all, by review alone;
- **the facts inside the code it changes**, which are external facts too: which
  fields a digest helper excludes, which recorded paths name files outside the
  run directory, where one field name covers two different relations;
- **each rule stated at the scope where it must hold**, and stated exclusive
  when the outcome is. A rule written under one criterion is applied to that
  criterion alone;
- **the smallest sufficient mechanism.** A criterion that demands a general
  capability where the problem is fixed and small buys the holes a general
  mechanism has and a specific one cannot.

A task that freezes part of the record has one more rule: a record never asserts
a property no measurement produced, nor an equivalent of what happened in place
of what happened. Pinning an exact field set with a boolean forces a value where
nothing was measured; keeping the measurement and the statement about the
measurement in two fields does not.

### 8. Choose the budget and the inference profile

Both are control inputs, and both belong in whatever you write down before the
run. `codeservo models` reports what each backend advertises on this machine;
read it before naming a model, an effort or a speed, and remember it is a dated
inventory rather than proof that an account may use what it lists.

The iteration budget decides what the run can establish. One iteration measures
a first actuation and nothing about feedback. More than one lets a failing gate
or a refusing sensor reach the actuator unchanged, which is the only way a run
observes convergence.

## The run

```bash
codeservo run \
  --repo /path/to/project \
  --task ./TASK.md \
  --state-dir /path/to/state \
  --max-iterations 2
```

Nothing else touches the repository while it runs. The controller measures the
source tree at the baseline, then works in an isolated checkout; a change made
to the source in between is measured by neither.

## After the run

### 9. Read the decision before the patch

`status` and `decision.reasons` say what the controller concluded and why. A
rejection names which control refused — scope, a gate, the frozen sensor, the
review, or an exhausted budget — and that is what to read next.

### 10. Verify the run directory

```bash
codeservo verify-run <run-directory>
```

`VALID`, `INVALID` or `INCOMPLETE`, in exit codes 0, 1 and 2. It recomputes
every digest the record claims, walks the event chain, and holds the last event
to the record's own verdict. Until it answers, a run directory is a report; once
it does, it is evidence. It verifies wherever the directory is copied, because
the paths inside it are relative.

### 11. Read what the gates measured, not only that they passed

A gate declaring `codeservo-json` leaves a document beside its logs saying what
it counted and what it found. A green gate set is a floor, and two runs are only
comparable through those numbers.

### 12. Read the review's criteria one by one

The review is a sensor, not the final authority, and its value is where the
gates are silent. A criterion marked `not_verifiable` says the repository did
not carry the evidence to decide it, which is a fact about the task as much as
about the change.

### 13. Inspect the exact diff

`ACCEPTED` means the controls the constitution names were satisfied. It does not
mean the change is the one you wanted, and an accepted patch stays a proposal
until its diff has been read.

### 14. Apply it, and measure the tree you applied it to

The run measured an isolated checkout of the base plus the patch. The tree you
apply to is the one that ships. Run the gates again there, and whatever else
holds that repository — a lockfile check, a reference comparison, continuous
integration — before the commit leaves your machine.

### 15. Keep the run directory

It carries the frozen task, the frozen constitution, the frozen sensor, every
gate's logs and documents, the actuation, the review and the chained journal. A
commit whose run directory was thrown away is a commit nobody can date against
the contract it satisfied.

## When a run is rejected

A rejected run closes rejected. Do not retry it unchanged, weaken a gate, reduce
a baseline, skip a test, or remove evidence protection to move past it: each of
those converts a refusal into a green record that establishes nothing.

Read which control refused first. A scope violation and a failing gate are about
the change. A sensor that failed on its own harness, or a review that found the
task ambiguous, are about the control inputs — and those are corrected by
opening a new base with a new sensor and a new task, not by amending the ones
the run already froze.
