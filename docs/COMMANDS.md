# Build, Test, and Development Commands

How the controller is measured. Every command below runs from this
repository's root. The rules those measurements enforce are in
[QUALITY.md](QUALITY.md).

Always run the relevant tests after modifying CodeServo. Never weaken a test,
gate, invariant, or architecture check to make an experiment pass.

## Reference Validation

For the controller, the reference validation is the locked Pixi environment,
and it is the same six measurements the repository's own gates name:

```bash
pixi lock --check --no-config
pixi run --locked --no-config lint          # ruff, on src, tests and tools
pixi run --locked --no-config types         # mypy, on the shipped tree
pixi run --locked --no-config test          # the suite
pixi run --locked --no-config compile       # byte compilation
pixi run --locked --no-config architecture  # the layer contract of .importlinter
pixi run --locked --no-config coverage      # the suite again, over the decision core
```

None of the six writes into the tree it measures: `ruff` and `lint-imports`
run with their caches disabled, `mypy` sends its cache to `/dev/null`, and
`coverage` declares a data file under the temporary directory, so a gate
cannot change the candidate it is only observing.

`coverage` reports on the decision core alone — the acceptance rules, the
constitution reader and run verification — and fails under 93 percent. The
package as a whole measures higher, but a total over the package would let a
well covered periphery answer for the code that decides.

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
rather than asserted. `tools/record_parity.py` drives five trajectories — an
accepted run, one converging on the second attempt, an exhausted budget, a
rejecting review, and one measuring through a provider — and captures the
shape of each record, its event sequence, the artefacts the run directory
holds and the `verify-run` verdict, with everything the clock or a temporary
location decides masked out. Capture before the change, capture after, and
compare:

```bash
python3.12 tools/record_parity.py capture /tmp/before.json
python3.12 tools/record_parity.py capture /tmp/after.json
python3.12 tools/record_parity.py compare /tmp/before.json /tmp/after.json
```

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
