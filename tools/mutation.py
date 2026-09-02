"""Mutation testing on the code whose silent degradation would cost the most.

Coverage says a line ran. It does not say that anything would have noticed had
the line been wrong. Mutation testing asks the second question: it changes one
operator, one constant or one comparison at a time, runs the tests that cover
that module, and counts the changes nothing objected to.

The scope is deliberately narrow, and it is the article's criterion applied to
this package: the acceptance rules, run verification, the event chain, and the
scope control. A surviving mutant there is a way the controller could start
deciding differently with every gate still green.

This is a maintainer command and cannot be a gate, for a reason that has
nothing to do with how long it takes. It edits the source file to make each
mutant, so it modifies the tree it measures, which is exactly what a gate may
never do. It runs in its own environment for a second reason: the tool brings
an HTTP client and an ORM with it, and the environment a confined measurement
runs in has no business holding either.

    pixi run -e mutation --locked --no-config mutation

Each target declares the survival rate above which it fails, read off this tree
with a short margin. The two high ceilings are a statement of where the suite
stands, not a target: they stop those modules getting quieter, and closing them
is work of its own.

The rate is not exact. One mutant of the journal flaps between runs on the same
tree, so a ceiling set at the reading would sometimes fail on a tree nothing
touched. Every ceiling carries a couple of mutants of room for that reason.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Target:
    """One module, the tests that cover it, and what it may not exceed."""

    module: str
    tests: str
    ceiling: float
    why: str


CORE = (
    Target(
        "src/codeservo/controller/decision.py",
        "test_decision.py",
        0.0,
        "the acceptance rules",
    ),
    Target(
        "src/codeservo/evidence/verify.py",
        "test_verify*.py",
        20.0,
        "the verdict on a run directory",
    ),
    Target(
        # Measured twice on one tree at 36.88 and 36.25 percent: one mutant of
        # this module flaps between runs, where the other three targets repeat
        # to the hundredth. The ceiling carries that, so the verdict does not.
        "src/codeservo/evidence/journal.py",
        "test_journal*.py",
        38.0,
        "the chained trajectory",
    ),
    Target(
        # The survivors are all on one guard against a malformed numstat line,
        # which `git diff --numstat` does not emit. Defensive parsing of another
        # program's output is worth keeping, and no test can reach it.
        "src/codeservo/sensors/scope.py",
        "test_scope.py",
        8.0,
        "the structural limits on a diff",
    ),
)


def _config(target: Target) -> str:
    return f"""[cosmic-ray]
module-path = "{ROOT / target.module}"
timeout = 120
test-command = "python -m unittest discover -s tests -p '{target.tests}'"
excluded-modules = []

[cosmic-ray.distributor]
name = "local"
"""


def _run(*command: str) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True)


def _mutated_sources() -> list[str]:
    """The tracked files under `src/` that differ from what Git holds.

    A mutant is applied to the file on disk and reverted afterwards, so an
    interrupted run leaves one behind — and a later measurement would then be
    taken against a tree nobody meant to write. Git is what says the tree is
    the one that was committed, so it is asked before and after.
    """
    done = _run("git", "diff", "--name-only", "--", "src")
    return [line for line in done.stdout.splitlines() if line.strip()]


def measure(target: Target, workspace: Path) -> tuple[float, bool]:
    """The survival rate of one target, and whether it stayed under its ceiling.

    The session database is written outside the repository: the tool already
    edits the tree to mutate it, and leaving a database behind as well would
    make the measurement visible in the thing measured.
    """
    name = Path(target.module).stem
    config = workspace / f"{name}.toml"
    session = workspace / f"{name}.sqlite"
    config.write_text(_config(target), encoding="utf-8")

    # The tests must pass before anything is mutated. A test command that
    # fails on its own makes every mutant look killed, and the run then
    # reports a perfect score for a measurement that took place on nothing.
    baseline = _run("cosmic-ray", "baseline", str(config))
    if baseline.returncode != 0:
        print(
            f"{name}: the tests do not pass unmutated, so nothing can be"
            f" measured\n{baseline.stdout}{baseline.stderr}",
            file=sys.stderr,
        )
        return float("nan"), False

    for step in ("init", "exec"):
        done = _run("cosmic-ray", step, str(config), str(session))
        if done.returncode != 0:
            print(f"{name}: cosmic-ray {step} failed\n{done.stderr}", file=sys.stderr)
            return float("nan"), False

    # The rate comes from the tool; the verdict is taken here. `cr-rate` has a
    # `--fail-over` flag, and a ceiling of zero disables it rather than
    # forbidding every survivor: it reads `if fail_over and rate > fail_over`,
    # and zero is falsy. A ceiling that silently measures nothing is worse than
    # no ceiling.
    rate = _run("cr-rate", str(session))
    survival = float(rate.stdout.strip())
    return survival, survival <= target.ceiling


def main() -> int:
    dirty = _mutated_sources()
    if dirty:
        print(
            "the tree already differs from the commit, so nothing measured here"
            " would be about the committed code: " + ", ".join(dirty),
            file=sys.stderr,
        )
        return 1

    exceeded: list[str] = []
    with tempfile.TemporaryDirectory(prefix="codeservo-mutation-") as tmp:
        workspace = Path(tmp)
        for target in CORE:
            rate, held = measure(target, workspace)
            verdict = "ok" if held else "OVER"
            print(
                f"{Path(target.module).stem:10} {rate:6.2f}%"
                f"  ceiling {target.ceiling:5.1f}%  {verdict:4}  {target.why}"
            )
            if not held:
                exceeded.append(target.module)

    left = _mutated_sources()
    if left:
        print(
            "\na mutant was left in the tree, so the run did not put it back:"
            " " + ", ".join(left),
            file=sys.stderr,
        )
        return 1

    if exceeded:
        print(
            "\nmutants survived above the ceiling in: " + ", ".join(exceeded),
            file=sys.stderr,
        )
        return 1
    print("\nevery target of the decision core is under its ceiling")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
