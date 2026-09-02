"""A ceiling on how long the two measurements the loop repeats may take.

Two durations decide what a run costs. The suite runs at every iteration of
the quick phase, so a suite that quietly triples makes every iteration cost
three times as much and eventually meets the gate's timeout — which ends the
run as a sensor fault rather than as a named refusal. And `verify-run` is what
an auditor runs over a record afterwards, so it has to stay something one can
run over a series of them.

    pixi run --locked --no-config duration

Three readings over those two subjects, and they are not the same kind of
control.

The total for the suite is the loosest, on purpose. It grows whenever tests
are added, which is exactly what should happen, so a tight ceiling here would
fire on healthy work and be raised without anyone reading it. It is set to
catch a change of order, not a change of percent.

The slowest single test is the one that names a regression. It does not move
when the suite grows, so a case that suddenly takes ten seconds is a case
doing something it did not do before.

One verification of a run directory is the third, taken as the median of
several so that one scheduling accident does not decide it.

Every ceiling is a wall clock, and a wall clock is the noisiest sensor in this
repository: it measures the machine as much as the tree, and the machines this
runs on differ by more than a factor of two. So the readings are printed
whether or not they held — a number in the log is what a later ceiling can be
set from — and the ceilings themselves carry the difference between the
fastest machine that runs them and the slowest.
"""

from __future__ import annotations

import io
import statistics
import sys
import tempfile
import time
import unittest
from dataclasses import dataclass
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests"), str(Path(__file__).parent)]

import observation  # noqa: E402

from codeservo.evidence.verify import verify_run  # noqa: E402
from run_fixtures import build_run  # noqa: E402

# How many verifications one reading is taken over. The command reads a
# directory and recomputes every digest in it, so a single call is short
# enough for the scheduler to decide it.
VERIFICATIONS = 25


@dataclass(frozen=True)
class Subject:
    """One duration, what it may not exceed, and what this tree reads."""

    name: str
    ceiling: float
    unit: str
    reading: str
    why: str



SUBJECTS = {
    "suite": Subject(
        "the whole suite",
        240.0,
        "s",
        "54 s",
        "every iteration of the quick phase pays it",
    ),
    "test": Subject(
        "the slowest test",
        8.0,
        "s",
        "1.9 s",
        "it names a case, where the total names the suite's size",
    ),
    "verification": Subject(
        "one verify-run",
        15.0,
        "ms",
        "1.3 ms",
        "a record is read one at a time and a series is read in one sitting",
    ),
}


def measure_suite() -> tuple[float, float, str, unittest.TestResult, str]:
    """Run the suite once, and read back the total and the slowest case.

    `unittest` times each case itself, cleanup included, and leaves the
    readings on the result. A suite that does not pass has not been timed but
    interrupted, so the caller refuses rather than reporting a duration taken
    over a different amount of work.
    """
    log = io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    runner = unittest.TextTestRunner(stream=log, verbosity=0)
    started = time.perf_counter()
    result = runner.run(suite)
    total = time.perf_counter() - started
    name, slowest = max(
        result.collectedDurations, key=lambda taken: taken[1],
        default=("no test ran", 0.0),
    )
    return total, slowest, name, result, log.getvalue()


def measure_verification() -> float:
    """The median of several verifications of one complete run directory."""
    with tempfile.TemporaryDirectory(prefix="codeservo-duration-") as tmp:
        run_dir = build_run(Path(tmp))
        readings = []
        for _ in range(VERIFICATIONS):
            started = time.perf_counter()
            verify_run(run_dir)
            readings.append((time.perf_counter() - started) * 1000)
    return statistics.median(readings)


def report(key: str, reading: float) -> bool:
    subject = SUBJECTS[key]
    held = reading <= subject.ceiling
    print(
        f"{subject.name:18} {reading:8.2f} {subject.unit:2}"
        f"  ceiling {subject.ceiling:7.1f} {subject.unit:2}"
        f"  {'ok' if held else 'OVER':4}"
        f"  read at {subject.reading:6} here  {subject.why}"
    )
    return held


def main(argv: list[str]) -> int:
    total, slowest, name, result, log = measure_suite()
    if not result.wasSuccessful():
        print(
            "the suite does not pass, so what it took is not a duration for"
            f" this tree\n{log}",
            file=sys.stderr,
        )
        return 1

    readings = {
        "suite": total,
        "test": slowest,
        "verification": measure_verification(),
    }
    over = [key for key, reading in readings.items() if not report(key, reading)]
    print(f"{'':18} {result.testsRun:8} tests  slowest: {name}")

    metrics: dict[str, float] = {"tests": float(result.testsRun)}
    for key, reading in readings.items():
        subject = SUBJECTS[key]
        metrics[f"{key}.{subject.unit}"] = round(reading, 3)
        metrics[f"{key}.ceiling"] = subject.ceiling
    observation.write(
        observation.location(argv),
        sensor="duration",
        passed=not over,
        summary=(
            f"{total:.1f} s for {result.testsRun} tests, slowest {slowest:.2f} s,"
            f" one verification {readings['verification']:.2f} ms"
        ),
        findings=[
            observation.finding(
                id=f"over-ceiling:{key}",
                severity=observation.MAJOR,
                message=(
                    f"{SUBJECTS[key].name} took {readings[key]:.2f}"
                    f" {SUBJECTS[key].unit}, ceiling"
                    f" {SUBJECTS[key].ceiling:g} {SUBJECTS[key].unit}"
                ),
            )
            for key in over
        ],
        metrics=metrics,
    )

    if over:
        print(
            "\nover the ceiling: " + ", ".join(SUBJECTS[key].name for key in over),
            file=sys.stderr,
        )
        return 1
    print("\nevery duration the loop repeats is under its ceiling")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
