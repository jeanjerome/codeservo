# Ratchets

A gate that passed can still decide against the candidate. This is where a
recurring defect stops being an instruction and becomes a control, without any
adapter having to reconstruct the state before the change.

A gate that reports what it measured can hold a metric of that report to a
direction across the change. The rule is declared beside the gate and the
controller applies it: it already holds the document the gate wrote about the
source tree at the baseline and the one it wrote about the candidate, so no
adapter has to reconstruct the state before the change to compare against.

```toml
[[gate]]
name = "coverage"
phase = "full"
task = "coverage"
timeout_seconds = 600
baseline = true
result_format = "codeservo-json"
ratchet = { line_coverage = ">=", missing = "<=" }
```

`<=` says the candidate's value may not exceed the baseline's, `>=` that it may
not fall below it, and an unchanged value always holds. A ratchet is read over
a gate that passed: a failing gate has already decided against the candidate,
and its document describes a different amount of work. When a ratchet breaks,
the candidate is not let through, the decision names the gate, the metric and
both values, and the actuator is told the same before the next iteration.

A ratchet is silent when either document lacks the metric, because a comparison
with a value nobody measured would be a verdict no measurement produced. That
silence is safe only while the adapter writing the metric is a protected path,
which is why `tools/**` sits under `protected` wherever a gate reports through
one. The constitution refuses a ratchet on a gate answering with its exit code
alone, and on a gate outside the baseline: neither could ever be compared.

## Declared on this repository

CodeServo holds two of its own. On the `coverage` gate, `line_coverage >=` and
`missing <=`: the share of the decision core the suite reaches may not fall,
and the number of its statements no test reaches may not rise. On the `unit`
gate, `tests >=` and `skipped <=`: a suite that stops discovering runs fewer
tests, and a test that stops running skips rather than fails, and neither shows
in an exit code.
