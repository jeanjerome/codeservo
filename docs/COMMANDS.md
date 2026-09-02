# Build, Test, and Development Commands

How the controller is measured. Every command below runs from this
repository's root. The rules those measurements enforce are in
[QUALITY.md](QUALITY.md).

Always run the relevant tests after modifying CodeServo. Never weaken a test,
gate, invariant, or architecture check to make an experiment pass.

## Reference Validation

For the controller, the reference validation is the locked Pixi environment,
and it is the same four measurements the repository's own gates name:

```bash
pixi lock --check --no-config
pixi run --locked --no-config lint     # ruff, on src and tests
pixi run --locked --no-config types    # mypy, on the shipped tree
pixi run --locked --no-config test     # the suite
pixi run --locked --no-config compile  # byte compilation
```

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
