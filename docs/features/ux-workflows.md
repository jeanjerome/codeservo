# UX Workflows Around the Loop

*Coming. Nothing here is implemented; this states the shape it has to have and
the properties it may not cost.*

There are two loops. The inner one decides about a candidate: one actuator, a
profile it cannot negotiate, a context that was frozen, a verdict computed from
evidence. The outer one decides whether the inner one is worth believing — does
this repository carry enough sensors for an `ACCEPTED` to mean anything, is
this task well formed, is this host even capable, has this finding come back.

Everything below belongs to the outer loop. None of it runs inside the inner
one, and that is what makes it possible at all.

## Why this costs the inner loop nothing

Before a run, **everything is a control input**, and what makes a control input
trustworthy is not who wrote it: it is that it was versioned before the
actuation it constrains. The controller refuses a dirty tree, `.codeservo/**`
is a protected path, and the constitution and the task are frozen and digested
at run start. From the record's point of view a constitution written by a
person and one written by an assistant are indistinguishable, and that is the
point of freezing rather than trusting.

After the decision, the record is closed and chained. A surface that reads it
cannot change it: `verify-run` only reads, every event carries the digest of
the one before it, and the decision is itself an event the chain closes over.

## The four moments

[RUNNING-A-CHANGE.md](../RUNNING-A-CHANGE.md) is the same ground as fifteen
manual steps. These are those steps grouped by what a person is actually
deciding, with the part of each that a machine can settle on its own.

### Assess — what this repository and this host can hold

A verdict rendered by three gates and a verdict rendered by twelve are not the
same statement, and nothing says so today. The experiments measured the
consequence: a set of green gates is a floor, not a verdict, and three
implementations were accepted with everything green, one of them carrying four
defects verified mechanically afterwards.

So the first thing a person needs is a **capability profile of the target
repository**: which kinds of sensor exist at all — unit, end to end, property,
mutation, fuzzing, architecture — which of them the constitution declares, what
share of the code that decides is reached, what a mutation run leaves alive.
Every one of those is a number or a fact, not an opinion.

The host is the same question one level down: which confinement mechanism this
machine carries, whether the agent CLIs are installed and authenticated,
whether the declared provider resolves. `codeservo doctor` already answers
that part, and it is the one piece of this document that exists.

Thresholds belong here too, and they are derived rather than asked:
`max_diff_lines` from the percentiles of the repository's own history,
`timeout_seconds` from the durations measured at the baseline, `max_iterations`
from what earlier runs needed.

**What stays outside**: the surface finds the gap; it does not write the tests.
A missing sensor becomes a task, the loop fills it, and the gates hold it.
Sensors written outside the loop are sensors nothing measured.

### Specify — a task and a sensor that will not fail for a known reason

This is where the tool is hardest to use and where it helps least. Every
first-iteration failure observed while self-hosting came from the control
inputs and not from the actuator, in nine shapes that are written down: a file
set excluding a module a constraint needed, a criterion needing a value the
documented shape had nowhere to hold, a criterion depending on an external fact
nothing could check, a sensor never replayed under the gate's confinement, and
five more.

Two of those shapes are what code intelligence answers. An assistant backed by
a language server can say which modules a criterion needs, what a symbol
actually touches, and whether the shape a criterion names has a slot for the
value it demands. That is worth more here than inside the loop, where the same
integration would cost the MCP-free guarantee, the frozen context and the
ecosystem agnosticism at once.

The sensor is the sensitive one, and not because of who writes it. A sensor is
the oracle, and the shape that cost the most was one never replayed the way a
gate runs it — four iterations and 18.10 USD chasing a fault in the sensor's own
harness, where the same capability landed on the first actuation once the sensor
had been replayed. So a sensor is not written: it is written, replayed under
confinement against the pinned base, shown failing for the reason it names, shown
able to pass, and then committed. **Automating the replay is worth more than
automating the writing.**

### Decide — three outcomes presented as facts

A run ends on `ACCEPTED`, `REJECTED` or `ESCALATED`, and each one asks something
different of a person. `ESCALATED` exists precisely to route a decision to a
human, and today it hands them a record and nothing else. `REJECTED` covers both
a candidate that failed and a control that could not measure — the record
separates them and a person has to dig for it.

What a surface adds here is not a summary. It is every claim naming the artefact
it came from, so that anything shown can be checked against the run directory.
If a screen cannot be derived from the record, the record is incomplete, and
that is a useful thing to find out.

### Land and learn — the patch, the message, and the control that is owed

`codeservo land` already applies the patch and commits it with the run, the base
and the patch digest in the body. The message is written by a person, and an
assistant drafts it from the diff and the task at no risk: the commit is a human
action and the record is not touched.

What follows is the part that closes the outer loop. The findings the review
reported go to a register with a column naming the gate that covers each one.
A finding type that appears twice is a gate that is owed, and that is where the
outer loop produces its own next task.

## The constraints that hold whatever the surface looks like

1. **The surface that writes control inputs is never the actuator, and its
   output passes through a human commit before the freeze.** If the model that
   implements a change also wrote its acceptance criteria and its sensor, the
   independence of the oracle is gone, and that independence is what everything
   else rests on. The existing mechanics already force the commit; the surface
   has to make it a decision rather than a formality.
2. **Nothing after the run re-decides.** A triage surface explains, orders and
   proposes the next control. It never writes into a run directory and never
   restates a status the record reached.
3. **What is expensive stays unskippable.** The failure mode is not that an
   assistant wrote a sensor; it is that a person accepted it without reading.
   The counter is the checks — the replay under confinement, the green base,
   each criterion naming its verification — and not more warnings.
4. **It is a separate surface, not a package here.** The controller is small,
   layered inward and held by mutation testing. An interactive, stateful,
   multi-agent assistant is the opposite kind of software, and putting it in
   `src/codeservo/` would cost exactly what makes the controller auditable.
   They share published artefacts, not a codebase.

## The contract those surfaces read

`observation.schema.json` and `review.schema.json` are published because a
target repository writes against them. `evidence.json` declares its shape
through `schema_version` and publishes nothing, which was tidy while the
controller was its only reader.

It stops being tidy here. Every one of the four moments reads a record, a
journal or the findings register, and a second reader that has to read this
package's source is coupled to it rather than to a contract. Publishing the
record schema is what turns the outer loop into something that can be built
without touching the inner one.
