# Features

What the controller implements, one document per notion. The
[front page](../../README.md) is the summary; [ARCHITECTURE.md](../ARCHITECTURE.md)
is how the code is shaped.

| | |
| --- | --- |
| [The loop, phase by phase](the-loop.md) | The whole diagram, and what freeze, actuate, measure, decide and record each stand for |
| [Sandboxed execution](sandboxed-execution.md) | The profile every actuator, reviewer and gate runs under, and the two mechanisms that apply it |
| [Feedback sensors](feedback-sensors.md) | Gates, the document a gate writes about what it measured, and the three report formats read without an adapter |
| [Ratchets](ratchets.md) | Holding a measured metric to a direction between the baseline and the candidate |
| [Structured output and the decision](structured-output.md) | What the reviewer answers, and the rule that turns it into one of three outcomes |
| [Context engineering](context-engineering.md) | What reaches the actuator, and what the feedback carries back |
| [Evidence](evidence.md) | The run directory, the record, the chained journal, the findings register, and what is measured |
| [Preparing a target repository](target-repository.md) | The constitution, the locked environment, and how a task is written |
| [Commands](commands.md) | The command line in full |
| [Self-hosting](self-hosting.md) | How the controller built itself, and why that stopped being the mode |
| [UX workflows around the loop](ux-workflows.md) · coming | What a person does before a run and after it, and the properties any surface doing it may not cost |

## Why this shape

The target repository owns the desired operating envelope
(`.codeservo/constitution.toml`). `TASK.md` supplies a temporary desired delta.
The coding agent is only an actuator. Tests, linters, scope constraints and
independent semantic review are sensors. CodeServo is the controller. Git is the
state substrate. `events.jsonl` and `evidence.json` are the audit record, and
`verify-run` is what makes that record answerable rather than merely stored.

The source is organised in those same terms, one package per role: `controller`
holds the loop and its phases, `actuators` the backend port and its two
adapters, `sensors` the gates, the scope check and the observation contract,
`workspace` the trees a run works on, `evidence` the record and its
verification, `policies` the constitution, `runtime` the process and its
confinement, and `domain` the values the other layers are written in terms of.
Dependencies point inward, so no adapter reaches the values a decision is
expressed in.
