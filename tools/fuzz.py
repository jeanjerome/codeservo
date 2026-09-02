"""Coverage-guided fuzzing of the boundaries a run is read through.

A property states a rule over every input of a shape a test author described.
A fuzzer looks for the input nobody described: it mutates bytes, watches which
branches they reach, and keeps what reached a new one. The two boundaries this
covers are the ones another party supplies — a configuration file and a run
directory handed to `verify-run` — plus the execution provider's own output,
which the controller reads before anything is measured.

The failure signal needs no oracle, because the product already defines it. A
constitution is read or refused by name; a record reaches `VALID`, `INVALID`
or `INCOMPLETE`, or is refused; a description gives four facts or a refusal.
An interpreter traceback is none of those, and it ends a run with the
actuation already applied and nothing recorded about it.

This is a gate, and the three things that make it one are here rather than in
the fuzzer's defaults. The seed and the run count are fixed, so the same tree
gives the same verdict twice. Crashing inputs are written under a temporary
directory, because libFuzzer otherwise drops them in the working directory —
into the tree the gate is only supposed to be measuring. And the coverage the
run gained is read back: an uninstrumented target reports no `cov:` at all and
still exits zero after every requested run, which is a green measuring
nothing.

    pixi run --locked --no-config fuzz            # the gate: bounded, seeded
    python tools/fuzz.py --search 300             # a longer search, per target

The search mode is a maintainer command. It takes a wall clock instead of a
run count and lets libFuzzer draw its own seed, so it can find what the gate's
budget does not reach. What it finds becomes a named case in the suite: the
crash is printed with its input, in hex and in Base64, above the artefact file
holding the same bytes.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import observation  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TARGET_DIR = Path(__file__).resolve().parent / "fuzz_targets"

# Seeds, and only seeds. A boundary reached through a decoder is unreachable
# from random bytes — the search would spend its whole budget failing to
# produce a document — so each target that takes one starts from documents
# shaped like the real thing and mutates outwards. libFuzzer adds to the
# corpus it is given, so the gate hands it a copy under the temporary
# directory and this one stays as committed. An input found to crash becomes a
# named case in the suite rather than a file here: the suite runs everywhere,
# and a corpus entry proves nothing about what it once caught.
CORPUS_DIR = Path(__file__).resolve().parent / "fuzz_corpus"

# Fixed, so that two runs on one tree agree. Any value works; this one is the
# day the fuzzing was introduced.
SEED = 20260902

# libFuzzer's own default. Stated here because it bounds what the record
# target can reach: the mutations it applies are drawn from these bytes.
MAX_LEN = 4096

# The last line of a completed run, and the coverage it reached. A run that
# instrumented nothing prints the same line without the `cov:` field.
DONE = re.compile(r"^#(?P<runs>\d+)\s+DONE\b(?:.*?\bcov: (?P<cov>\d+))?", re.M)


@dataclass(frozen=True)
class Target:
    """One boundary, and how far the gate searches it.

    The budgets differ by what one input costs. Reading a constitution or a
    description is parsing; verifying a record rewrites a file and recomputes
    every digest of a run directory, which is a thousand times slower.
    """

    name: str
    runs: int
    boundary: str


# Read off this tree: the three together take some fifteen seconds, which is
# what a gate in the full phase can spend. A budget is not a ceiling to stay
# under but a search to pay for, so it is set by what the loop can afford and
# raised when it can afford more.
TARGETS = (
    Target("constitution", 60000, "the control input a run is read from"),
    Target("provider_description", 60000, "what the execution provider printed"),
    Target("run_record", 6000, "the record verify-run distrusts"),
)


@dataclass(frozen=True)
class Outcome:
    """What one target's run established, and what it failed to establish."""

    runs: int
    coverage: int
    verdict: str
    log: str

    @property
    def held(self) -> bool:
        return self.verdict == "ok"


def _seeds(target: Target, workspace: Path) -> Path | None:
    """The target's committed seeds, copied where the fuzzer may write."""
    seeds = CORPUS_DIR / target.name
    if not seeds.is_dir():
        return None
    working = workspace / "corpus" / target.name
    shutil.copytree(seeds, working)
    return working


def _command(
    target: Target, artifacts: Path, seeds: Path | None, seconds: int | None
) -> list[str]:
    """The fuzzer's command line, with everything a gate needs made explicit.

    `-B` keeps the interpreter from writing bytecode caches next to the code
    it imports, so the measurement leaves the tree exactly as it found it.
    """
    command = [
        sys.executable,
        "-B",
        str(TARGET_DIR / f"{target.name}.py"),
        f"-artifact_prefix={artifacts}/{target.name}-",
        f"-max_len={MAX_LEN}",
    ]
    if seconds is None:
        command.extend([f"-runs={target.runs}", f"-seed={SEED}"])
    else:
        command.append(f"-max_total_time={seconds}")
    if seeds is not None:
        command.append(str(seeds))
    return command


def measure(target: Target, workspace: Path, seconds: int | None) -> Outcome:
    """Run one target, and read back what the run actually established.

    Three things are refused, and the second and third are the ones a fuzz run
    fails silently on. A non-zero exit is a crashing input. A missing `DONE`
    line means the target never got as far as fuzzing. And a `DONE` line with
    no coverage on it means nothing was instrumented, so every input took the
    same path and the search was a random draw reported as a pass.
    """
    artifacts = workspace / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    # The targets keep their scratch directories under the temporary location,
    # so an aborted run leaves nothing behind once this workspace is removed.
    environment = {**os.environ, "TMPDIR": str(workspace)}
    done = subprocess.run(
        _command(target, artifacts, _seeds(target, workspace), seconds),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    log = done.stdout + done.stderr
    # The last one: libFuzzer prints a `DONE` line for each corpus pass.
    reported = list(DONE.finditer(log))
    final = reported[-1] if reported else None

    if done.returncode != 0:
        return Outcome(0, 0, "CRASHED", log)
    if final is None:
        return Outcome(0, 0, "NOT RUN", log)
    if final["cov"] is None:
        return Outcome(int(final["runs"]), 0, "NOT MEASURED", log)
    return Outcome(int(final["runs"]), int(final["cov"]), "ok", log)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--search",
        type=int,
        metavar="SECONDS",
        help="search each boundary for this long instead of running the gate's"
        " fixed budget, with a seed the fuzzer draws itself",
    )
    parser.add_argument(
        "document",
        nargs="?",
        help="where to write the observation, when the controller asked for one",
    )
    parsed = parser.parse_args()
    seconds = parsed.search

    failed: list[str] = []
    metrics: dict[str, float] = {}
    findings = []
    with tempfile.TemporaryDirectory(prefix="codeservo-fuzz-") as tmp:
        workspace = Path(tmp)
        for target in TARGETS:
            outcome = measure(target, workspace, seconds)
            print(
                f"{target.name:22} {outcome.runs:7} runs"
                f"  cov {outcome.coverage:5}  {outcome.verdict:12}"
                f"  {target.boundary}"
            )
            metrics[f"{target.name}.runs"] = outcome.runs
            metrics[f"{target.name}.coverage"] = outcome.coverage
            if not outcome.held:
                failed.append(target.name)
                findings.append(
                    observation.finding(
                        id=f"{outcome.verdict.lower().replace(' ', '-')}:{target.name}",
                        severity=observation.BLOCKER,
                        message=f"{target.boundary}: {outcome.verdict.lower()}",
                    )
                )
                # The report and the log it belongs to go to two streams, so
                # the first is flushed before the second is written.
                sys.stdout.flush()
                print(outcome.log.strip()[-4000:], file=sys.stderr)

    executed = sum(metrics[f"{t.name}.runs"] for t in TARGETS)
    observation.write(
        observation.location([parsed.document] if parsed.document else []),
        sensor="fuzz",
        passed=not failed,
        summary=f"{len(TARGETS)} boundaries searched over {executed} inputs",
        findings=findings,
        metrics=metrics,
    )

    if failed:
        print(
            "\nthe boundary held no answer for an input reaching: "
            + ", ".join(failed),
            file=sys.stderr,
        )
        return 1
    print("\nevery boundary answered, for every input the search reached")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
