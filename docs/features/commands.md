# Commands

```bash
codeservo doctor [--repo <repo>]          # what this host provides, and what is missing
codeservo init [repo]                     # add a starter constitution
codeservo run --task ./TASK.md --model <model> --effort <level>   # run one controlled change
codeservo models                          # list the models a run may request, and their list prices
codeservo verify-run <run-directory>      # verify one run directory
codeservo land <run-directory>            # land an accepted run on the repository it measured
```

### doctor

```bash
codeservo doctor [--repo /path/to/project] [--state-dir <dir>] [--json]
```

Says what a run would reach for and what this host answers: the interpreter,
Git, the confinement mechanism, each agent CLI, and the state directory. With
`--repo` it also reads the target repository — a Git work tree, a clean one,
the gates its constitution declares, and whether the provider it names is
installed.

Every reading is taken by asking rather than by inferring: the mechanism by
applying a profile, a tool by running it, the repository by reading what it
declares. A version is what an installed CLI looks like and says nothing about
a session being authenticated, which only a call would establish and which this
command does not make.

The exit status is 1 when something a run needs is absent and 0 otherwise. A
reading a run does not need is reported the same way and decides nothing: one
absent actuator narrows the choice, it does not stop a run.

### run

```bash
codeservo run --repo /path/to/project --task ./TASK.md --model claude-sonnet-5 --effort medium
```

The exit status is the decision: 0 for `ACCEPTED`, 1 for `REJECTED`, 2 for
`ESCALATED`. A usage error also exits 2, and honestly so: nothing was decided
there either, and a person reads what was printed.

```bash
codeservo run \
  --repo /path/to/project \
  --task ./TASK.md \
  --state-dir /path/to/codeservo-state \
  --max-iterations 4 \
  --agent-timeout-seconds 1800 \
  --actuator claude --model <model> --effort low|medium|high|xhigh \
  --review-actuator codex --review-model <model> --review-effort <level>
```

An inference profile is a backend, a model and an effort, and a run names all
three. `--model` is the complete identifier of a model the catalogue lists for
`--actuator`, never an alias such as `opus`, so a CLI update cannot silently
select another version. `--effort` is one of the four levels, handed to the CLI
unchanged; whether a model accepts it is the CLI's to decide, and it fails
explicitly when it does not. The reviewer's profile defaults to the
implementer's and can differ on every field, so a Codex implementation can be
decided by a Claude review or the reverse. A model the catalogue does not list,
or lists for the other backend, is refused by name before the run directory
exists. The record keeps the requested profile beside the observed one and never
fills the second from the first.

### land

```bash
codeservo land <run-directory> [--message "feat: health endpoint"] [--json]
```

Applies an accepted run's `change.patch` to the repository the run measured and
commits it there, with the run, the base commit and the patch digest in the
commit body. It refuses everything that would make the commit a change nothing
measured: a run directory that does not verify, a run that was not accepted, a
run already landed, a repository whose head is no longer the base commit the
run froze, or one holding uncommitted work.

The record is not touched. The integration is one more event, `run.landed`,
appended to the run's journal after the decision and chained like every other,
and `verify-run` reads it as the one event allowed there: a landing that names
another base, another patch, or a run that was not accepted is invalid, and
one altered afterwards breaks the chain. The findings the review reported on
the landed candidate go to `<state-dir>/findings/<repository>.tsv`, one line
each, with `none` in the last column until a gate covers them and a person
writes its name there.

### models

```bash
codeservo models [--actuator claude|codex] [--model <model>] [--json]
```

Lists the catalogue the package publishes: every model a run may request, the
backend that drives it, and the list prices its tokens are rated at, in USD per
million tokens, dated and sourced. No agent starts and no provider cache is
read. The catalogue is `templates/models.toml`, copied into the package; a run
freezes the copy it was rated by beside its task and constitution, and
`verify-run` holds the record to it.

### What a run consumed, and what it cost

Each actuation records what the backend reported it consumed, in the five
categories both backends count: uncached input, cache reads, cache writes,
output, and the reasoning part of the output. The two CLIs spell them
differently, and each adapter puts every count under the same name. The
controller rates them at the frozen catalogue's list prices, block by block,
and the record carries the rated cost beside whatever cost the backend itself
reported, never merged. Codex names no model, so its tokens are rated at the
requested one and the record says the attribution is the controller's. A block
the catalogue cannot rate, a model it does not list or a cache duration it has
no price for, leaves the cost unknown rather than understated. A list price is
a measure comparable across backends and runs, not an invoice.

### verify-run

```bash
codeservo verify-run <run-directory> [--json]
```

Decides about a run directory from that directory alone.

```text
VALID       every required artefact is present and matches
INVALID     a digest or a relation is false
INCOMPLETE  the record predates a proof this contract requires, or the run never finished
```

Exit status is 0, 1 and 2 in that order, and 3 when the argument holds no
readable `evidence.json`. The command only reads: it writes nothing and never
rewrites the status a run recorded.

It checks the frozen task and constitution, the frozen sensors, the environment
inventory, every artefact the record names by a path and a digest, the digests a
record recomputes from itself, the journal's chain, and the agreement between
the last two events and the recorded decision. A record edited after the fact
cannot make a rejected run look accepted, because the decision is itself an
event the chain closes over.
