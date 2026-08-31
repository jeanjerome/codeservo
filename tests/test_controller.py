import json
import tempfile
import unittest
from pathlib import Path

from codeservo.controller import (
    ControlFailure,
    _altered_sensors,
    _observations,
    _resolve_state_dir,
    _review_schema_path,
)
from codeservo.evidence import sha256_file, sha256_path
from codeservo.model import Constitution, Gate, ReviewPolicy, ScopePolicy

OBSERVATION_FIELDS = {
    "phase",
    "name",
    "kind",
    "sensor",
    "passed",
    "exit_code",
    "timed_out",
    "duration_ms",
    "stdout_sha256",
    "stderr_sha256",
    "result_sha256",
    "stdout_tail",
    "stderr_tail",
}


class StateDirectoryTests(unittest.TestCase):
    def test_rejects_state_directory_inside_target_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp).resolve()

            with self.assertRaisesRegex(ControlFailure, "outside the target repository"):
                _resolve_state_dir(repo, repo / ".codeservo-state")


class SensorIntegrityTests(unittest.TestCase):
    def _frozen(self, root: Path) -> tuple[dict, dict]:
        sensor = root / "sensors" / "acceptance"
        sensor.mkdir(parents=True)
        (sensor / "contract.py").write_text("assert True\n", encoding="utf-8")
        return (
            {"acceptance": sensor},
            {"acceptance": {"sha256": sha256_path(sensor)}},
        )

    def test_reports_nothing_while_the_snapshot_is_intact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths, evidence = self._frozen(Path(temp))

            self.assertEqual([], _altered_sensors(paths, evidence))

    def test_reports_a_snapshot_a_gate_wrote_into(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths, evidence = self._frozen(Path(temp))
            (paths["acceptance"] / "__pycache__").mkdir()
            (paths["acceptance"] / "__pycache__" / "contract.pyc").write_bytes(b"\x00")

            self.assertEqual(["acceptance"], _altered_sensors(paths, evidence))


class ObservationBundleTests(unittest.TestCase):
    def _constitution(self) -> Constitution:
        return Constitution(
            path=Path(".codeservo/constitution.toml"),
            raw_text="",
            scope=ScopePolicy(),
            gates=(
                Gate(name="unit", phase="quick", command="make test"),
                Gate(
                    name="acceptance",
                    phase="quick",
                    command="run-sensor",
                    baseline=False,
                    sensor="owner/acceptance",
                ),
                Gate(name="compile", phase="full", command="make check"),
            ),
            review=ReviewPolicy(),
        )

    def _gate_result(self, name: str, out_dir: Path, stdout: str = "") -> dict:
        out_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = out_dir / f"{name}.stdout.log"
        stderr_path = out_dir / f"{name}.stderr.log"
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return {
            "name": name,
            "command": f"secret command for {name}",
            "passed": True,
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 7,
            "stdout_path": str(stdout_path),
            "stdout_sha256": "a" * 64,
            "stderr_path": str(stderr_path),
            "stderr_sha256": "b" * 64,
            "result_sha256": "c" * 64,
        }

    def test_orders_quick_gates_before_full_gates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            quick = [
                self._gate_result("unit", run_dir),
                self._gate_result("acceptance", run_dir),
            ]
            full = [self._gate_result("compile", run_dir)]

            bundle = _observations(self._constitution(), quick, full, (run_dir,))

            self.assertEqual(1, bundle["schema_version"])
            self.assertEqual(
                [
                    ("quick", "unit"),
                    ("quick", "acceptance"),
                    ("full", "compile"),
                ],
                [(gate["phase"], gate["name"]) for gate in bundle["gates"]],
            )
            for gate in bundle["gates"]:
                self.assertEqual(OBSERVATION_FIELDS, set(gate))
                self.assertTrue(gate["passed"])

    def test_classifies_gates_from_the_frozen_constitution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            # A repository gate named after a sensor stays a repository gate.
            quick = [
                self._gate_result("unit", run_dir),
                self._gate_result("acceptance", run_dir, "external sensor output\n"),
            ]

            bundle = _observations(self._constitution(), quick, [], (run_dir,))

            unit, acceptance = bundle["gates"]
            self.assertEqual("repository_gate", unit["kind"])
            self.assertIsNone(unit["sensor"])
            self.assertEqual("external_sensor", acceptance["kind"])
            self.assertEqual("owner/acceptance", acceptance["sensor"])

    def test_exposes_no_command_or_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "run"
            worktree = Path(temp) / "worktree"
            quick = [
                self._gate_result(
                    "acceptance",
                    run_dir,
                    f"sensor at {run_dir}/sensors/acceptance in {worktree}\n",
                )
            ]

            bundle = _observations(
                self._constitution(), quick, [], (run_dir, worktree)
            )

            serialized = json.dumps(bundle)
            self.assertNotIn("secret command", serialized)
            self.assertNotIn(str(run_dir), serialized)
            self.assertNotIn(str(worktree), serialized)
            self.assertEqual(
                "sensor at <redacted>/sensors/acceptance in <redacted>",
                bundle["gates"][0]["stdout_tail"],
            )

    def test_keeps_only_the_last_logged_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            emitted = [f"line {index}" for index in range(500)]
            quick = [
                self._gate_result("unit", run_dir, "\n".join(emitted) + "\n")
            ]

            bundle = _observations(self._constitution(), quick, [], (run_dir,))

            tail_lines = bundle["gates"][0]["stdout_tail"].splitlines()
            self.assertEqual(120, len(tail_lines))
            self.assertEqual(emitted[-120:], tail_lines)
            self.assertEqual("", bundle["gates"][0]["stderr_tail"])


class ReviewSchemaTests(unittest.TestCase):
    def test_prefers_the_repository_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository_copy = root / "templates" / "review.schema.json"
            repository_copy.parent.mkdir()
            repository_copy.write_text("{}", encoding="utf-8")

            self.assertEqual(repository_copy, _review_schema_path(root))

    def test_falls_back_to_the_packaged_schema_without_repository_templates(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            packaged = _review_schema_path(Path(temp))

            self.assertTrue(packaged.is_file(), f"missing packaged schema: {packaged}")
            schema = json.loads(packaged.read_text(encoding="utf-8"))
            self.assertEqual({"criteria", "findings"}, set(schema["required"]))
            self.assertEqual(
                {"criteria", "findings"}, set(schema["properties"])
            )

    def test_both_copies_state_the_same_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            packaged = _review_schema_path(Path(temp))
        repository_copy = _review_schema_path()

        self.assertNotEqual(packaged, repository_copy)
        self.assertEqual(sha256_file(packaged), sha256_file(repository_copy))


if __name__ == "__main__":
    unittest.main()
