import tempfile
import unittest
from pathlib import Path

from codeservo.config import ConstitutionError, load_constitution


EXECUTION = """
[execution]
provider = "pixi"
manifest = "pyproject.toml"
environment = "default"
"""

GATES = """
[[gate]]
name = "quick"
phase = "quick"
command = "true"

[[gate]]
name = "full"
phase = "full"
command = "true"
"""


class ConstitutionTests(unittest.TestCase):
    def _write(self, body: str, *, workspace: bool = False) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)
        cfg = repo / ".codeservo" / "constitution.toml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(body, encoding="utf-8")
        if workspace:
            (repo / "pyproject.toml").write_text("", encoding="utf-8")
            (repo / "pixi.lock").write_text("version: 6\n", encoding="utf-8")
        return repo

    def test_requires_quick_and_full_gates(self) -> None:
        repo = self._write(
            '''
[[gate]]
name = "only"
phase = "quick"
command = "true"
'''
        )
        with self.assertRaisesRegex(ConstitutionError, "full gate"):
            load_constitution(repo)

    def test_accepts_quick_and_full_gates(self) -> None:
        repo = self._write(
            '''
[[gate]]
name = "quick"
phase = "quick"
command = "true"

[[gate]]
name = "full"
phase = "full"
command = "true"
'''
        )
        constitution = load_constitution(repo)
        self.assertEqual(1, len(constitution.gates_for("quick")))
        self.assertEqual(1, len(constitution.gates_for("full")))

    def test_requires_external_sensor_for_nonbaseline_gate(self) -> None:
        repo = self._write(
            '''
[[gate]]
name = "acceptance"
phase = "quick"
command = "false"
baseline = false

[[gate]]
name = "full"
phase = "full"
command = "true"
'''
        )

        with self.assertRaisesRegex(ConstitutionError, "external sensor"):
            load_constitution(repo)

    def test_external_sensor_cannot_run_during_baseline(self) -> None:
        repo = self._write(
            '''
[[gate]]
name = "acceptance"
phase = "quick"
command = "false"
sensor = "example/acceptance"

[[gate]]
name = "full"
phase = "full"
command = "true"
'''
        )

        with self.assertRaisesRegex(ConstitutionError, "requires baseline=false"):
            load_constitution(repo)

    def test_rejects_gate_name_that_can_escape_log_directory(self) -> None:
        repo = self._write(
            '''
[[gate]]
name = "../acceptance"
phase = "quick"
command = "true"

[[gate]]
name = "full"
phase = "full"
command = "true"
'''
        )

        with self.assertRaisesRegex(ConstitutionError, "invalid gate name"):
            load_constitution(repo)


class ExecutionEnvironmentTests(unittest.TestCase):
    """The declared execution environment, and every way of misdeclaring it."""

    _write = ConstitutionTests._write

    def test_declares_no_provider_by_default(self) -> None:
        repo = self._write(GATES)

        self.assertIsNone(load_constitution(repo).execution)

    def test_resolves_the_manifest_and_its_lockfile(self) -> None:
        repo = self._write(EXECUTION + GATES, workspace=True)

        execution = load_constitution(repo).execution

        self.assertEqual("pixi", execution.provider)
        self.assertEqual("pyproject.toml", execution.manifest)
        self.assertEqual("pixi.lock", execution.lock)
        self.assertEqual("default", execution.environment)

    def test_locates_the_lockfile_beside_the_manifest(self) -> None:
        repo = self._write(
            EXECUTION.replace("pyproject.toml", "sub/pixi.toml") + GATES
        )
        (repo / "sub").mkdir()
        (repo / "sub" / "pixi.toml").write_text("", encoding="utf-8")
        (repo / "sub" / "pixi.lock").write_text("version: 6\n", encoding="utf-8")

        execution = load_constitution(repo).execution

        self.assertEqual("sub/pixi.toml", execution.manifest)
        self.assertEqual("sub/pixi.lock", execution.lock)

    def test_defaults_the_environment_name(self) -> None:
        repo = self._write(
            EXECUTION.replace('environment = "default"\n', "") + GATES,
            workspace=True,
        )

        self.assertEqual("default", load_constitution(repo).execution.environment)

    def test_rejects_any_other_provider(self) -> None:
        repo = self._write(
            EXECUTION.replace('"pixi"', '"conda"') + GATES, workspace=True
        )

        with self.assertRaisesRegex(ConstitutionError, "provider must be pixi"):
            load_constitution(repo)

    def test_rejects_an_absolute_manifest(self) -> None:
        repo = self._write(
            EXECUTION.replace('"pyproject.toml"', '"/etc/pyproject.toml"') + GATES,
            workspace=True,
        )

        with self.assertRaisesRegex(ConstitutionError, "under the repository root"):
            load_constitution(repo)

    def test_rejects_a_manifest_escaping_the_repository(self) -> None:
        repo = self._write(
            EXECUTION.replace('"pyproject.toml"', '"../pyproject.toml"') + GATES,
            workspace=True,
        )

        with self.assertRaisesRegex(ConstitutionError, "under the repository root"):
            load_constitution(repo)

    def test_rejects_a_missing_manifest(self) -> None:
        repo = self._write(EXECUTION + GATES)

        with self.assertRaisesRegex(ConstitutionError, "missing manifest"):
            load_constitution(repo)

    def test_requires_a_lockfile_beside_the_manifest(self) -> None:
        repo = self._write(EXECUTION + GATES, workspace=True)
        (repo / "pixi.lock").unlink()

        with self.assertRaisesRegex(ConstitutionError, "requires pixi.lock"):
            load_constitution(repo)

    def test_rejects_an_environment_name_outside_the_character_class(self) -> None:
        repo = self._write(
            EXECUTION.replace('"default"', '"../default"') + GATES, workspace=True
        )

        with self.assertRaisesRegex(ConstitutionError, "execution environment name"):
            load_constitution(repo)


class GateMeasurementTests(unittest.TestCase):
    """A gate declares a command or a task, and exactly one of them."""

    _write = ConstitutionTests._write

    def test_accepts_a_task_gate_beside_a_shell_gate(self) -> None:
        repo = self._write(
            EXECUTION
            + '''
[[gate]]
name = "unit"
phase = "quick"
task = "test-unit"

[[gate]]
name = "full"
phase = "full"
command = "true"
''',
            workspace=True,
        )

        quick, full = load_constitution(repo).gates

        self.assertEqual(("test-unit", None), (quick.task, quick.command))
        self.assertEqual((None, "true"), (full.task, full.command))

    def test_rejects_a_gate_declaring_both_a_command_and_a_task(self) -> None:
        repo = self._write(
            EXECUTION
            + '''
[[gate]]
name = "unit"
phase = "quick"
command = "true"
task = "test-unit"

[[gate]]
name = "full"
phase = "full"
command = "true"
''',
            workspace=True,
        )

        with self.assertRaisesRegex(ConstitutionError, "gate unit: declares both"):
            load_constitution(repo)

    def test_rejects_a_gate_declaring_neither(self) -> None:
        repo = self._write(
            '''
[[gate]]
name = "unit"
phase = "quick"

[[gate]]
name = "full"
phase = "full"
command = "true"
'''
        )

        with self.assertRaisesRegex(ConstitutionError, "gate unit: declares neither"):
            load_constitution(repo)

    def test_rejects_a_task_gate_without_a_declared_provider(self) -> None:
        repo = self._write(
            '''
[[gate]]
name = "unit"
phase = "quick"
task = "test-unit"

[[gate]]
name = "full"
phase = "full"
command = "true"
'''
        )

        with self.assertRaisesRegex(
            ConstitutionError, "gate unit: task requires an .execution. provider"
        ):
            load_constitution(repo)

    def test_rejects_a_task_name_outside_the_character_class(self) -> None:
        repo = self._write(
            EXECUTION
            + '''
[[gate]]
name = "unit"
phase = "quick"
task = "../test-unit"

[[gate]]
name = "full"
phase = "full"
command = "true"
''',
            workspace=True,
        )

        with self.assertRaisesRegex(
            ConstitutionError, "invalid task name for gate unit"
        ):
            load_constitution(repo)


if __name__ == "__main__":
    unittest.main()
