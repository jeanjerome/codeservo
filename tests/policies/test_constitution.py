import tempfile
import unittest
from pathlib import Path

from codeservo.domain.constitution import Direction, Ratchet, ResultFormat
from codeservo.policies.constitution import ConstitutionError, load_constitution

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

    def test_refuses_a_constitution_that_is_not_text(self) -> None:
        """Bytes no decoder accepts are a control input, refused by name."""
        repo = self._write("version = 1\n")
        (repo / ".codeservo" / "constitution.toml").write_bytes(b"\xff\xfe[scope]")

        with self.assertRaisesRegex(ConstitutionError, "not readable as text"):
            load_constitution(repo)

    def test_requires_quick_and_full_gates(self) -> None:
        repo = self._write(
            """
[[gate]]
name = "only"
phase = "quick"
command = "true"
"""
        )
        with self.assertRaisesRegex(ConstitutionError, "full gate"):
            load_constitution(repo)

    def test_accepts_quick_and_full_gates(self) -> None:
        repo = self._write(
            """
[[gate]]
name = "quick"
phase = "quick"
command = "true"

[[gate]]
name = "full"
phase = "full"
command = "true"
"""
        )
        constitution = load_constitution(repo)
        self.assertEqual(1, len(constitution.gates_for("quick")))
        self.assertEqual(1, len(constitution.gates_for("full")))

    def test_requires_external_sensor_for_nonbaseline_gate(self) -> None:
        repo = self._write(
            """
[[gate]]
name = "acceptance"
phase = "quick"
command = "false"
baseline = false

[[gate]]
name = "full"
phase = "full"
command = "true"
"""
        )

        with self.assertRaisesRegex(ConstitutionError, "external sensor"):
            load_constitution(repo)

    def test_external_sensor_cannot_run_during_baseline(self) -> None:
        repo = self._write(
            """
[[gate]]
name = "acceptance"
phase = "quick"
command = "false"
sensor = "example/acceptance"

[[gate]]
name = "full"
phase = "full"
command = "true"
"""
        )

        with self.assertRaisesRegex(ConstitutionError, "requires baseline=false"):
            load_constitution(repo)

    def test_rejects_gate_name_that_can_escape_log_directory(self) -> None:
        repo = self._write(
            """
[[gate]]
name = "../acceptance"
phase = "quick"
command = "true"

[[gate]]
name = "full"
phase = "full"
command = "true"
"""
        )

        with self.assertRaisesRegex(ConstitutionError, "invalid gate name"):
            load_constitution(repo)


class GateResultFormatTests(unittest.TestCase):
    """What a gate declares it answers with, beside its exit code."""

    def _write(self, body: str) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)
        cfg = repo / ".codeservo" / "constitution.toml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(body, encoding="utf-8")
        (repo / "pyproject.toml").write_text("", encoding="utf-8")
        (repo / "pixi.lock").write_text("version: 6\n", encoding="utf-8")
        return repo

    def test_an_undeclared_gate_answers_with_its_exit_code(self) -> None:
        constitution = load_constitution(self._write(GATES))

        self.assertEqual(
            ["exit-code", "exit-code"],
            [gate.result_format for gate in constitution.gates],
        )

    def test_accepts_the_two_declared_values(self) -> None:
        repo = self._write(
            """
[[gate]]
name = "quick"
phase = "quick"
command = "true"
result_format = "codeservo-json"

[[gate]]
name = "full"
phase = "full"
command = "true"
result_format = "exit-code"
"""
        )

        constitution = load_constitution(repo)

        self.assertEqual(
            ["codeservo-json", "exit-code"],
            [gate.result_format for gate in constitution.gates],
        )

    def test_the_key_is_independent_of_every_other_one(self) -> None:
        repo = self._write(
            EXECUTION
            + """
[[gate]]
name = "quick-task"
phase = "quick"
task = "check"
result_format = "codeservo-json"

[[gate]]
name = "acceptance"
phase = "quick"
command = "true"
baseline = false
sensor = "example/acceptance"
result_format = "codeservo-json"

[[gate]]
name = "full"
phase = "full"
command = "true"
result_format = "codeservo-json"
"""
        )

        constitution = load_constitution(repo)

        self.assertEqual(
            ["codeservo-json"] * 3,
            [gate.result_format for gate in constitution.gates],
        )
        # A phase, a task, a baseline and an external sensor, all unchanged.
        self.assertEqual("check", constitution.gates[0].task)
        self.assertFalse(constitution.gates[1].baseline)
        self.assertEqual("example/acceptance", constitution.gates[1].sensor)
        self.assertEqual("full", constitution.gates[2].phase)

    def test_rejects_any_other_value_naming_it(self) -> None:
        repo = self._write(
            """
[[gate]]
name = "quick"
phase = "quick"
command = "true"
result_format = "tap"

[[gate]]
name = "full"
phase = "full"
command = "true"
"""
        )

        with self.assertRaisesRegex(ConstitutionError, "tap"):
            load_constitution(repo)


class GateReportTests(unittest.TestCase):
    """Where a `junit-xml` gate's tool writes its reports, and the misdeclarations."""

    _write = ConstitutionTests._write

    def _gates(self, quick: str) -> str:
        return f"""
[[gate]]
name = "quick"
phase = "quick"
command = "true"
{quick}

[[gate]]
name = "full"
phase = "full"
command = "true"
"""

    def test_reads_the_pattern_of_a_junit_gate(self) -> None:
        repo = self._write(
            self._gates(
                'result_format = "junit-xml"\n'
                'reports = "**/target/surefire-reports/TEST-*.xml"'
            )
        )

        constitution = load_constitution(repo)

        self.assertEqual(ResultFormat.JUNIT_XML, constitution.gates[0].result_format)
        self.assertEqual(
            "**/target/surefire-reports/TEST-*.xml", constitution.gates[0].reports
        )
        self.assertIsNone(constitution.gates[1].reports)

    def test_a_junit_gate_names_its_reports(self) -> None:
        repo = self._write(self._gates('result_format = "junit-xml"'))

        with self.assertRaisesRegex(ConstitutionError, "requires reports"):
            load_constitution(repo)

    def test_only_a_junit_gate_names_reports(self) -> None:
        for declared in ("", 'result_format = "codeservo-json"\n'):
            with self.subTest(declared or "exit-code"):
                repo = self._write(self._gates(declared + 'reports = "reports/*.xml"'))

                with self.assertRaisesRegex(
                    ConstitutionError, 'reports requires result_format = "junit-xml"'
                ):
                    load_constitution(repo)

    def test_the_pattern_stays_under_the_measured_tree(self) -> None:
        for pattern, wrong in (
            ("/tmp/reports/*.xml", "must stay under"),
            ("../reports/*.xml", "must stay under"),
            ("target/../../*.xml", "must stay under"),
            ("   ", "names no file"),
        ):
            with self.subTest(pattern):
                repo = self._write(
                    self._gates(f'result_format = "junit-xml"\nreports = "{pattern}"')
                )

                with self.assertRaisesRegex(ConstitutionError, wrong):
                    load_constitution(repo)

    def test_the_pattern_is_a_string(self) -> None:
        repo = self._write(self._gates('result_format = "junit-xml"\nreports = 3'))

        with self.assertRaisesRegex(ConstitutionError, "reports must be a string"):
            load_constitution(repo)

    def test_a_ratchet_holds_the_metrics_of_a_projected_document(self) -> None:
        repo = self._write(
            self._gates(
                'result_format = "junit-xml"\n'
                'reports = "reports/*.xml"\n'
                'ratchet = { failures = "<=", tests = ">=" }'
            )
        )

        self.assertEqual(
            (("failures", "<="), ("tests", ">=")),
            tuple(
                (ratchet.metric, ratchet.direction)
                for ratchet in load_constitution(repo).gates[0].ratchets
            ),
        )


class GateRatchetTests(unittest.TestCase):
    """The metrics a gate holds between the baseline and the candidate."""

    _write = ConstitutionTests._write

    def _gates(self, quick: str) -> str:
        return f"""
[[gate]]
name = "quick"
phase = "quick"
command = "true"
{quick}

[[gate]]
name = "full"
phase = "full"
command = "true"
"""

    def test_a_gate_declares_no_ratchet_by_default(self) -> None:
        constitution = load_constitution(self._write(GATES))

        self.assertEqual([(), ()], [gate.ratchets for gate in constitution.gates])

    def test_reads_each_metric_with_its_direction_in_order(self) -> None:
        repo = self._write(
            self._gates(
                'result_format = "codeservo-json"\n'
                'ratchet = { missing = "<=", line_coverage = ">=" }'
            )
        )

        constitution = load_constitution(repo)

        self.assertEqual(
            (
                Ratchet(metric="missing", direction=Direction.AT_MOST),
                Ratchet(metric="line_coverage", direction=Direction.AT_LEAST),
            ),
            constitution.gates[0].ratchets,
        )
        self.assertEqual((), constitution.gates[1].ratchets)

    def test_a_quoted_metric_is_read_as_written(self) -> None:
        repo = self._write(
            self._gates(
                'result_format = "codeservo-json"\n'
                'ratchet = { "whole suite.seconds" = "<=" }'
            )
        )

        self.assertEqual(
            (Ratchet(metric="whole suite.seconds", direction=Direction.AT_MOST),),
            load_constitution(repo).gates[0].ratchets,
        )

    def test_requires_a_document_to_compare(self) -> None:
        repo = self._write(self._gates('ratchet = { missing = "<=" }'))

        with self.assertRaisesRegex(ConstitutionError, "codeservo-json"):
            load_constitution(repo)

    def test_requires_a_baseline_measurement(self) -> None:
        repo = self._write(
            self._gates(
                "baseline = false\n"
                'sensor = "example/acceptance"\n'
                'result_format = "codeservo-json"\n'
                'ratchet = { missing = "<=" }'
            )
        )

        with self.assertRaisesRegex(ConstitutionError, "baseline"):
            load_constitution(repo)

    def test_refuses_a_ratchet_that_is_not_a_table(self) -> None:
        repo = self._write(
            self._gates('result_format = "codeservo-json"\nratchet = "<="')
        )

        with self.assertRaisesRegex(ConstitutionError, "must be a table"):
            load_constitution(repo)

    def test_refuses_a_ratchet_naming_no_metric(self) -> None:
        repo = self._write(
            self._gates('result_format = "codeservo-json"\nratchet = {}')
        )

        with self.assertRaisesRegex(ConstitutionError, "names no metric"):
            load_constitution(repo)

    def test_refuses_an_empty_metric_name(self) -> None:
        repo = self._write(
            self._gates('result_format = "codeservo-json"\nratchet = { "" = "<=" }')
        )

        with self.assertRaisesRegex(ConstitutionError, "empty metric"):
            load_constitution(repo)

    def test_refuses_a_direction_it_does_not_know_naming_it(self) -> None:
        repo = self._write(
            self._gates('result_format = "codeservo-json"\nratchet = { missing = "<" }')
        )

        with self.assertRaisesRegex(ConstitutionError, "<=, >=, not '<'"):
            load_constitution(repo)

    def test_refuses_a_direction_that_is_not_a_string(self) -> None:
        repo = self._write(
            self._gates('result_format = "codeservo-json"\nratchet = { missing = 1 }')
        )

        with self.assertRaisesRegex(ConstitutionError, "must be a string"):
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
        repo = self._write(EXECUTION.replace("pyproject.toml", "sub/pixi.toml") + GATES)
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

        with self.assertRaisesRegex(
            ConstitutionError, "provider must be one of pixi, mise, not 'conda'"
        ):
            load_constitution(repo)

    def test_resolves_a_mise_manifest_and_its_lockfile(self) -> None:
        repo = self._write(
            EXECUTION.replace('"pixi"', '"mise"').replace("pyproject.toml", "mise.toml")
            + GATES
        )
        (repo / "mise.toml").write_text('[tools]\njava = "21"\n', encoding="utf-8")
        (repo / "mise.lock").write_text("[tools]\n", encoding="utf-8")

        execution = load_constitution(repo).execution

        self.assertEqual("mise", execution.provider)
        self.assertEqual("mise.toml", execution.manifest)
        self.assertEqual("mise.lock", execution.lock)

    def test_a_mise_manifest_requires_its_own_lockfile(self) -> None:
        repo = self._write(
            EXECUTION.replace('"pixi"', '"mise"').replace("pyproject.toml", "mise.toml")
            + GATES
        )
        (repo / "mise.toml").write_text('[tools]\njava = "21"\n', encoding="utf-8")
        (repo / "pixi.lock").write_text("version: 6\n", encoding="utf-8")

        with self.assertRaisesRegex(
            ConstitutionError, "provider mise requires mise.lock"
        ):
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
            + """
[[gate]]
name = "unit"
phase = "quick"
task = "test-unit"

[[gate]]
name = "full"
phase = "full"
command = "true"
""",
            workspace=True,
        )

        quick, full = load_constitution(repo).gates

        self.assertEqual(("test-unit", None), (quick.task, quick.command))
        self.assertEqual((None, "true"), (full.task, full.command))

    def test_rejects_a_gate_declaring_both_a_command_and_a_task(self) -> None:
        repo = self._write(
            EXECUTION
            + """
[[gate]]
name = "unit"
phase = "quick"
command = "true"
task = "test-unit"

[[gate]]
name = "full"
phase = "full"
command = "true"
""",
            workspace=True,
        )

        with self.assertRaisesRegex(ConstitutionError, "gate unit: declares both"):
            load_constitution(repo)

    def test_rejects_a_gate_declaring_neither(self) -> None:
        repo = self._write(
            """
[[gate]]
name = "unit"
phase = "quick"

[[gate]]
name = "full"
phase = "full"
command = "true"
"""
        )

        with self.assertRaisesRegex(ConstitutionError, "gate unit: declares neither"):
            load_constitution(repo)

    def test_rejects_a_task_gate_without_a_declared_provider(self) -> None:
        repo = self._write(
            """
[[gate]]
name = "unit"
phase = "quick"
task = "test-unit"

[[gate]]
name = "full"
phase = "full"
command = "true"
"""
        )

        with self.assertRaisesRegex(
            ConstitutionError, "gate unit: task requires an .execution. provider"
        ):
            load_constitution(repo)

    def test_rejects_a_task_name_outside_the_character_class(self) -> None:
        repo = self._write(
            EXECUTION
            + """
[[gate]]
name = "unit"
phase = "quick"
task = "../test-unit"

[[gate]]
name = "full"
phase = "full"
command = "true"
""",
            workspace=True,
        )

        with self.assertRaisesRegex(
            ConstitutionError, "invalid task name for gate unit"
        ):
            load_constitution(repo)


if __name__ == "__main__":
    unittest.main()
