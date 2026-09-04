# Self-Hosting, and Why It Stopped

From 0.1.0 to 0.6.0, CodeServo developed CodeServo. Every behavioural change in
those releases was proposed by a run of the previous frozen version, against a
pre-registered task and an external acceptance sensor written before the
actuation it constrains, and was accepted only by that version's gates and its
independent reviewer. A frozen version is installed outside this repository and
never imports code from the candidate it is measuring, so a generation never
controls its own construction.

It is no longer the implementation mode. Driving the controller's own changes
through the loop cost run time and tokens out of proportion with what the loop
had to decide, and the sharpest result of the track came from what a
specification said rather than from the loop deciding it. A change to the
controller is now made directly and held by this repository's own gates, which
is why 0.7.0 is the first version not built by a run of the one before it.

What remains is a periodic verification: a run at intervals, from a frozen
version, establishing that the tool still works end to end — freeze, isolation,
gates, feedback, review, decision, evidence. It obeys every rule of a
controlled change without exception, because a verification that skipped one
would establish nothing. The
two that froze 0.7.0 ran at a zero iteration budget, so no actuator process
started, and they still rejected a gate of this repository over a defect no
direct run of that gate could show.

Two things stay maintainer work by construction. Control inputs — the
constitution, the external sensors, the task — cannot come from the actuator
that is measured against them; `.codeservo/**` is a protected path for exactly
that reason. And two early changes that made the test suite compose with gate
and review isolation were made by hand, because the loop could not build the
bridge it needed in order to measure itself.

One further change was maintainer work by choice rather than by construction.
The package was reorganised into the layers
[ARCHITECTURE.md](../ARCHITECTURE.md) describes and the loop split into its
phases outside the loop, because driving a structural refactor through it cost
run time and tokens out of proportion with what it had to decide. It alters no
behaviour a gate measures: the record keeps the same fields, the same event
sequence and the same artefacts, and a run recorded before the change still
verifies.
