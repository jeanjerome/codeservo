# Structured Output and the Decision

The reviewer is a sensor, not a judge. It answers a JSON schema the package
publishes — `review.schema.json`, frozen into the run beside the task — with a
status for every acceptance criterion left to it and a list of typed findings.
It renders no verdict, and a verdict it tried to render would decide nothing:
the controller computes the outcome from those fields alone.

Each review sees one iteration's candidate and nothing of the reviews before
it, and is asked only about the criteria the task did not hand to a gate. What
it says about a criterion a gate already decided is kept in the record and
decides nothing.

## The rule

A candidate is `ACCEPTED` only when all of the following hold:

1. Both inference profiles were accepted before any checkout existed.
2. The original repository baseline was green.
3. The source repository was and remained clean during baseline.
4. Scope invariants pass.
5. Every quick gate passes within the iteration budget, and no ratchet a quick gate declares is broken.
6. Every full gate passes, and no ratchet a full gate declares is broken.
7. No frozen sensor changed, and no measurement phase changed the candidate.
8. The independent review returns exactly the acceptance criteria left to it, each as `satisfied`.
9. The review contains no finding whose severity is configured as blocking.

A criterion naming a gate is decided by rules 5 and 6, one left to the review
by rule 8, and no criterion is decided twice.

A candidate is `ESCALATED` when rules 1 to 7 hold and the review alone leaves
it undecided: a criterion left to the review that the reviewer could not
verify, with nothing else to correct; a criterion a gate passed that the
reviewer reports as not satisfied; or an iteration budget spent on review
objections alone, every gate and ratchet green. Nothing is fed back in those
cases, because nothing there is the candidate's to correct.

Anything else is `REJECTED`.

## Why a schema rather than a prose answer

A reviewer answering in prose has to be interpreted, and interpretation is
where a harness starts deciding by inference. A criterion status is one of a
closed set, a finding carries a severity the constitution declares blocking or
not, and the module that reads them is small enough to be held by mutation
testing. What the model contributes is evidence; what the controller
contributes is the verdict.
