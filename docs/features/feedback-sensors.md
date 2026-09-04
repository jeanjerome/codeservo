# Feedback Sensors

Deterministic quality gates — compilers, linters, type checkers, test suites,
coverage, mutation, fuzzing, architecture rules — wired into the loop so that a
failure triggers the next iteration before any commit is made. A gate answers
with an exit code, and that stays the verdict. What follows is the second
answer: a document saying what it measured, which is what makes feedback
specific and what a [ratchet](ratchets.md) reads.

A gate declares its phase. `quick` gates run on every iteration; `full` gates
run once the quick ones passed. `baseline = true` means the gate is also
measured on the source tree before anything is actuated, so a red repository
ends the run instead of blaming the candidate for it.

## A gate that reports what it measured

A gate answers with an exit code, and that stays the verdict. A gate that also
declares `result_format = "codeservo-json"` answers a second time, with a
document saying what it measured.

```toml
[[gate]]
name = "coverage"
phase = "full"
task = "coverage"
timeout_seconds = 600
baseline = true
result_format = "codeservo-json"
```

The controller creates a location it owns — outside the run directory and
outside the tree that gate measures — tells the gate where to write, validates
what was written against the six-field contract published at
`observation.schema.json`, and keeps that document byte for byte in the record
beside the gate's logs.

```json
{
  "schema_version": 1,
  "sensor": "coverage",
  "status": "passed",
  "summary": "87.50 percent of 240 statements, floor 85",
  "findings": [
    {
      "id": "below-floor:src/checkout/pricing.py",
      "severity": "info",
      "path": "src/checkout/pricing.py",
      "line": null,
      "message": "78.30 percent covered, under the floor of 85"
    }
  ],
  "metrics": { "line_coverage": 87.5, "statements": 240, "floor": 85.0 }
}
```

The location reaches the two kinds of gate by two channels, because only one
reaches each. A gate naming a shell command reads it from
`CODESERVO_OBSERVATION_PATH`. A gate naming a provider task is handed it as
the task's one argument, which the provider passes through: a pixi task starts
with an environment the provider cleans, so no variable would reach it, and a
mise task — which does inherit its environment — is handed the argument too, so
one adapter serves both. The target repository writes an adapter that takes a
location, not one that knows where the location came from.

## A gate whose tool already reports

A tool that already writes JUnit XML, SARIF or LCOV needs no adapter. The gate
declares `result_format = "junit-xml"` for test results, `"sarif"` for analysis
results or `"lcov"` for coverage tracefiles and, in `reports`, where its tool
writes them, as a pattern relative to the tree the gate measures:

```toml
[[gate]]
name = "test"
phase = "full"
task = "test"
timeout_seconds = 300
baseline = true
result_format = "junit-xml"
reports = "**/target/surefire-reports/TEST-*.xml"
```

The gate is told nothing. The controller lists the files the pattern matches
before the gate runs and again after it, reads the ones this measurement wrote
— new, or rewritten since — and projects them onto the same six-field document.
A test report becomes the counts of tests, failures, errors, skipped and
seconds, with one finding per failed or errored case. An analysis report
becomes the counts of results, errors, warnings, notes and suppressed, with one
finding per result the tool reported and did not suppress. A coverage tracefile
becomes the counts of lines, branches and functions found and covered, the
share of each, and one finding per file no test reached at all. All three carry
the status the exit code reached and name where each finding points.

The tool writes where it always writes, which is inside the tree it measures,
so **the target repository has to ignore that location**. A baseline gate that
leaves a tracked file behind has changed the tree it was only measuring, and
the run says so rather than reading the report.

A report the gate left as it found it is not read, and the summary says how
many were left. The projection is kept beside the gate's logs like a document
the gate wrote itself, and a ratchet reads its metrics. A gate that passed and
wrote no report measured nothing anyone can see, and that is a fault of the
sensor; one that failed and wrote none failed before its tool reported
anything, and its document says so. A report whose own content says its tool did not finish is
a fault too: a SARIF log whose `invocations` say so, and an LCOV tracefile
stopping inside a record, both report the same nothing as a clean measurement.
Nothing is deleted from the tree to make any of this true.

A document that is absent, malformed, or that contradicts the exit code is a
fault of the sensor and not a failure of the candidate: the run ends there, on
that classification alone, and nothing is fed back to the actuator. The schema
is published for adapters to read and is never executed.

Writing the adapter is the target repository's business, and it is where the
controller stays agnostic: the tool keeps its own output format, and a script
in the repository projects that output onto the six fields. Two properties are
worth holding it to. The tool's own output must stay on the streams the
controller handed the gate — it is what gets fed back when a gate fails, and a
suite can recognise that it runs confined through the directory of its own
descriptors. And the exit code must stay the tool's: an adapter that decided a
verdict would be the thing being measured.
