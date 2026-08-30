# What the experiments established

Four controlled experiments have run through this controller against one small
FastAPI repository. This file records what they established, what they did not,
and what follows for the controller's design. Run evidence lives outside this
repository, under `runs/deployment-tracker/` in the state repository; run
identifiers below point at it.

Read the limits section before reusing any of this. The sample is one codebase,
one task family, and one actuator family.

## Established

### Mechanical isolation is not optional

The first design told the actuator, in its prompt, not to read the acceptance
sensors. That separation is a request, and a request is not a control property.
Sensors are now unreachable through an operating-system profile the controller
owns, and the actuator's own tool calls hit `EPERM` on them.

The same reasoning applies to the reviewer: it is read-only because every write
to the candidate worktree is denied, not because it was asked to behave.

### A confined actuator must not sandbox itself

macOS refuses to apply a seatbelt profile inside another one. An actuator that
applies its own sandbox inside the controller's profile fails in a way that is
easy to misread: the agent process reports success while producing an empty
patch, and the controller records a successful actuation followed by failing
sensors.

Whatever confines an actuator must therefore be single: the controller profile,
with the agent's own sandbox disabled. The controller records which paths that
profile denies, so the property is auditable after the fact rather than assumed.

### Gates must not write into the record they produce

A gate running `pytest` against a frozen sensor snapshot wrote a bytecode cache
inside it. Nothing failed, but the sensor digest recorded at freeze time no
longer matched the snapshot on disk, so the evidence was no longer verifiable
from the run directory.

Gate processes now run under the controller profile with the run directory
read-only, and the controller recomputes every sensor digest after the quick and
full gates. Both properties hold whatever language a gate command runs, which a
per-ecosystem setting would not.

### A green gate set is a floor, not a verdict

This is the strongest result so far. Three implementations of the same task were
accepted with syntax, unit tests, architecture, full tests and two independent
acceptance sensors all green. The weakest of them contained, verified
mechanically after the run:

- a response model defined and referenced nowhere else in the tree;
- unused exception imports left in a test module;
- exception handlers made unreachable, which would have emitted the previous
  response shape;
- an exception declared as a frozen slotted dataclass whose generated
  initializer never populates `args`, so `copy.copy()` and `pickle` raise
  `TypeError` on it.

Gates measure what they encode and nothing else. A controller built on gates
bounds the failure modes it was given, and says nothing about the rest.

Evidence: `20260830T164007399565Z`.

### Actuator strength changes defect density, not acceptance

The same frozen task, sensors, reviewer and base commit were run with three
implementation models. All three converged on the first actuator iteration and
were accepted with every acceptance criterion satisfied. What separated them was
what sat below the gates: one, one, and six non-blocking review findings, from
strongest to weakest model, and a cost ranging from $1.56 down to $0.29.

Evidence: `20260830T161703191852Z`, `20260830T162915752980Z`,
`20260830T164007399565Z`.

### First-attempt failure came from the specification, not the model

An accepted change left a real under-specification: in a batch carrying several
kinds of failure, the response status depended on item order. The next task
stated the missing rule explicitly. All three models then satisfied it on their
first attempt, including the model that had missed it when it was unwritten, and
including models weaker than that one.

On this repository, what governs first-attempt failure is the completeness of the
specification, not the strength of the actuator. The controller's leverage is
therefore in the sensor: a deterministic sensor is where a requirement that the
task forgot to state becomes observable.

### Semantic review is useful and not reproducible

Two accepted implementations of the same task shared the same defect: neither
declared the new response body in the route, so both generate an incomplete API
document. One review reported it; the other did not, and reported instead a
removal from the module's public surface that the first had no occasion to see.

Semantic review earns its place by finding what no gate encodes, and disqualifies
itself as an arbiter by not finding the same thing twice. It stays a sensor whose
output the controller turns into a mechanical decision, never a verdict the
controller adopts.

### Evidence must record resolved facts, not requested ones

A model alias such as `opus` moves over time, so two runs naming the same alias
can have run different models. Sessions now record the identifier they resolved
to and the tokens every model spent. The same reasoning produced relative paths,
digests over logs, agent events and gate outcomes, and the frozen task and
constitution stored inside the run.

A pre-registration is only verifiable if the task and its sensor were versioned
before the actuation they describe. Freezing them inside the run makes the run
self-describing; versioning them beforehand makes the claim checkable.

## Not established

- **Feedback-driven convergence without a planted defect.** Six runs on tasks
  carrying no artificial failure converged on the first actuator iteration, so
  the controller emitted no feedback in any of them. The only demonstrated
  feedback loop used a prescribed initial defect. The controller's central
  claim — that mechanical feedback drives an actuator toward an acceptable state
  — remains untested outside that setup.
- **Generality.** One repository, around two thousand lines, one architecture
  check, one task family, one actuator family. Nothing here transfers by
  argument to a large codebase or another language.
- **Any reliability claim in a statistical sense.** No configuration was repeated,
  so single-run outcomes carry no confidence interval. The findings above are
  either mechanical properties, which hold by construction and are tested, or
  single observations, which are labelled as such.
- **The Codex backend end to end.** Its confinement starts correctly, but no
  complete run has finished on it since the actuator abstraction landed.

## Consequences for the design

1. Gates are authoritative and review is advisory, now for a demonstrated reason
   rather than a stylistic one.
2. Every separation the controller relies on is mechanical, or it is not a
   control property. Prompts state intent; profiles enforce it.
3. The controller's value concentrates in the sensor. Improving the loop means
   encoding more of what tasks leave unsaid, not asking the actuator for more.
4. Acceptance bounds what the gates encode. Reporting a run as accepted without
   its findings would overstate what was measured.
