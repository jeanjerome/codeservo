"""Measuring through a toolchain mise pins, kept outside every tree."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from codeservo.evidence.digests import sha256_file
from harness import (
    COMPILE_COMMAND,
    MISE_SENSOR_TASK,
    MISE_TASK,
    MISE_TOOL,
    MISE_TOOL_VERSION,
    MISE_VARIABLES,
    Case,
    build_case,
    constitution,
)

FORBIDDING = (
    "MISE_OFFLINE",
    "MISE_LOCKED",
    "MISE_AUTO_INSTALL",
    "MISE_EXEC_AUTO_INSTALL",
    "MISE_NOT_FOUND_AUTO_INSTALL",
    "MISE_TASK_RUN_AUTO_INSTALL",
)


class MiseE2ETests(unittest.TestCase):
    """A run measuring through a provider that installs once, outside the trees."""

    def _case(
        self,
        root: Path,
        *,
        task: str = MISE_TASK,
        implementer: str = "implement(ACCEPTABLE)",
        constitution_text: str | None = None,
        **overrides,
    ) -> Case:
        return build_case(
            root,
            implementer=implementer,
            provider=True,
            provider_name="mise",
            constitution_text=(
                constitution(
                    execution="default",
                    quick_task=task,
                    provider_name="mise",
                    sensor_task=MISE_SENSOR_TASK,
                )
                if constitution_text is None
                else constitution_text
            ),
            **overrides,
        )

    def _run(
        self, case: Case, log: Path, *, env: dict[str, str] | None = None, **overrides
    ) -> dict:
        return case.run(
            env={"CODESERVO_TEST_MISE_LOG": str(log), **(env or {})}, **overrides
        )

    def _calls(self, log: Path) -> list[dict]:
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    def _subcommands(self, log: Path) -> list[str]:
        return [call["args"][0] for call in self._calls(log)]

    @staticmethod
    def _data_dir(case: Case) -> Path:
        # As the controller names it: resolved, like every path it records.
        return case.state_dir.resolve() / "providers" / "mise"

    @staticmethod
    def _command(name: str, gates: list[dict]) -> str:
        return next(gate["command"] for gate in gates if gate["name"] == name)

    def test_freezes_the_toolchain_and_measures_through_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            log = root / "mise.log"

            result = self._run(case, log)

            self.assertEqual("ACCEPTED", result["status"])
            evidence = json.loads(Path(result["run_dir"], "evidence.json").read_text())
            environment = evidence["environment"]
            self.assertEqual("mise", environment["provider"])
            self.assertEqual(
                "2026.9.1-test test-os-test-arch", environment["provider_version"]
            )
            self.assertEqual("mise.toml", environment["manifest_path"])
            self.assertEqual("mise.lock", environment["lock_path"])
            self.assertEqual("default", environment["environment"])
            self.assertEqual("test-os-test-arch", environment["platform"])
            self.assertEqual(
                [MISE_TASK, MISE_SENSOR_TASK], environment["declared_tasks"]
            )
            self.assertEqual(
                sha256_file(case.repo / "mise.toml"), environment["manifest_sha256"]
            )
            self.assertEqual(
                sha256_file(case.repo / "mise.lock"), environment["lock_sha256"]
            )
            stored = Path(result["run_dir"], environment["packages_path"])
            self.assertEqual(
                [
                    {
                        "name": MISE_TOOL,
                        "version": MISE_TOOL_VERSION,
                        "requested": MISE_TOOL_VERSION[:1],
                        "installed": False,
                    }
                ],
                json.loads(stored.read_text()),
            )
            self.assertEqual(sha256_file(stored), environment["packages_sha256"])
            self.assertEqual(1, environment["package_count"])

    def test_each_gate_names_the_tree_it_measures(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            log = root / "mise.log"

            result = self._run(case, log)

            repo = Path(result["repo"])
            worktree = Path(result["worktree"])
            quick_gates = result["iterations"][-1]["quick_gates"]
            self.assertEqual(
                f"mise run -q -C '{repo}' '{MISE_TASK}'",
                self._command("syntax", result["baseline"]),
            )
            self.assertEqual(
                f"mise run -q -C '{worktree}' '{MISE_TASK}'",
                self._command("syntax", quick_gates),
            )
            # The sensor gate is a task like any other, and its command names
            # neither the sensor nor where it is.
            sensor = self._command("task-outcome", quick_gates)
            self.assertEqual(
                f"mise run -q -C '{worktree}' '{MISE_SENSOR_TASK}'", sensor
            )
            self.assertNotIn("sensors", sensor)
            # A shell gate is built and run exactly as it is without a provider.
            self.assertEqual(
                COMPILE_COMMAND,
                self._command("full", result["iterations"][-1]["full_gates"]),
            )

    def test_a_sensor_gate_naming_a_task_is_told_where_the_sensor_is(self) -> None:
        # The sensor task reads `$CODESERVO_SENSOR_PATH/README.md` and then
        # the candidate: a task inherits the variables the gate is started
        # with, so the location reaches it without appearing in any command.
        for implementer, verdict, passed in (
            ("implement(ACCEPTABLE)", "ACCEPTED", True),
            ("implement(UNACCEPTABLE)", "REJECTED", False),
        ):
            with self.subTest(verdict), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                case = self._case(root, implementer=implementer)
                log = root / "mise.log"

                result = self._run(case, log, max_iterations=1)

                self.assertEqual(verdict, result["status"])
                sensor = next(
                    gate
                    for gate in result["iterations"][-1]["quick_gates"]
                    if gate["name"] == "task-outcome"
                )
                self.assertEqual(passed, sensor["passed"])
                self.assertEqual(0 if passed else 1, sensor["exit_code"])

    def test_installs_once_before_the_baseline_and_never_into_a_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            log = root / "mise.log"

            result = self._run(case, log)

            self.assertEqual("ACCEPTED", result["status"])
            subcommands = self._subcommands(log)
            # Frozen, then installed, then measured; nothing after that but
            # measurements.
            self.assertEqual(["ls", "version", "tasks", "install"], subcommands[:4])
            self.assertEqual({"run"}, set(subcommands[4:]))
            install = next(c for c in self._calls(log) if c["args"][0] == "install")
            self.assertEqual(["install", "-C", result["repo"]], install["args"])
            # Into the controller's directory, under the state directory.
            installed = (
                self._data_dir(case) / "installs" / MISE_TOOL / MISE_TOOL_VERSION
            )
            self.assertTrue(installed.is_dir())
            for call in self._calls(log):
                self.assertEqual(
                    str(self._data_dir(case)), call["env"]["MISE_DATA_DIR"]
                )
            # Neither tree holds anything mise owns, and the source is as found.
            for tree in (case.repo, Path(result["worktree"])):
                self.assertFalse((tree / "installs").exists())
                self.assertFalse((tree / ".mise").exists())
                self.assertFalse((tree / "mise.local.toml").exists())
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=case.repo,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual("", status.stdout)

    def test_records_what_the_installation_did(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            log = root / "mise.log"

            result = self._run(case, log)

            worktree = Path(result["worktree"])
            candidate = result["environment"]["candidate"]
            self.assertEqual(
                ["mise", "install", "-C", result["repo"]], candidate["command"]
            )
            self.assertEqual(0, candidate["exit_code"])
            self.assertGreaterEqual(candidate["duration_ms"], 0)
            self.assertEqual(
                str(self._data_dir(case) / "installs"), candidate["prefix_path"]
            )
            self.assertTrue(Path(candidate["prefix_path"]).is_dir())
            # The digests are the candidate's own files, taken once it exists.
            self.assertEqual(
                sha256_file(worktree / "mise.toml"), candidate["manifest_sha256"]
            )
            self.assertEqual(
                sha256_file(worktree / "mise.lock"), candidate["lock_sha256"]
            )
            self.assertIsNone(candidate["config_sha256"])
            self.assertTrue(candidate["unchanged_at_end"])
            evidence = json.loads(Path(result["run_dir"], "evidence.json").read_text())
            self.assertFalse(
                Path(evidence["environment"]["candidate"]["prefix_path"]).is_absolute()
            )

    def test_refuses_a_manifest_that_moved_past_its_lockfile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root, stale_lock=True)
            log = root / "mise.log"
            manifest = sha256_file(case.repo / "mise.toml")
            lock = sha256_file(case.repo / "mise.lock")

            result = self._run(case, log)

            self.assertEqual("REJECTED", result["status"])
            [reason] = result["decision"]["reasons"]
            self.assertIn("mise.lock is not consistent with the manifest", reason)
            self.assertIn(
                f"{MISE_TOOL}@{MISE_TOOL_VERSION[:1]} is not in the lockfile", reason
            )
            # Before the baseline, before any checkout, and before mise was
            # asked anything at all.
            self.assertNotIn("baseline", result)
            self.assertIsNone(result["worktree"])
            self.assertEqual([], result["iterations"])
            self.assertEqual([], self._subcommands(log))
            self.assertFalse(self._data_dir(case).joinpath("installs").exists())
            self.assertEqual(manifest, sha256_file(case.repo / "mise.toml"))
            self.assertEqual(lock, sha256_file(case.repo / "mise.lock"))
            environment = result["environment"]
            self.assertEqual(manifest, environment["manifest_sha256"])
            self.assertNotIn("packages_path", environment)

    def test_refuses_a_task_the_manifest_does_not_declare(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root, task="absent-task")
            log = root / "mise.log"

            result = self._run(case, log)

            self.assertEqual("REJECTED", result["status"])
            self.assertIn("absent-task", result["decision"]["reasons"][0])
            self.assertNotIn("baseline", result)
            self.assertIsNone(result["worktree"])
            # Nothing was installed, and no task ever ran.
            self.assertEqual(["ls", "version", "tasks"], self._subcommands(log))

    def test_refuses_any_environment_but_the_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(
                root,
                constitution_text=constitution(
                    execution="prod",
                    quick_task=MISE_TASK,
                    provider_name="mise",
                    sensor_task=MISE_SENSOR_TASK,
                ),
            )
            log = root / "mise.log"

            result = self._run(case, log)

            self.assertEqual("REJECTED", result["status"])
            self.assertIn(
                "mise declares no environment prod", result["decision"]["reasons"][0]
            )
            self.assertEqual([], self._subcommands(log))

    def test_a_refused_installation_ends_the_run_before_the_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            log = root / "mise.log"

            result = self._run(
                case, log, env={"CODESERVO_TEST_MISE_INSTALL_FAILS": "1"}
            )

            self.assertEqual("REJECTED", result["status"])
            [reason] = result["decision"]["reasons"]
            self.assertIn("execution environment: installing default", reason)
            self.assertIn("mise ERROR failed to install", reason)
            # Nothing was measured, in either tree.
            self.assertNotIn("baseline", result)
            self.assertIsNone(result["worktree"])
            self.assertEqual([], result["iterations"])
            self.assertEqual(1, result["environment"]["candidate"]["exit_code"])
            self.assertEqual(
                ["ls", "version", "tasks", "install"], self._subcommands(log)
            )

    def test_every_measurement_is_offline_locked_and_reads_one_manifest(self) -> None:
        # A shell gate of a provider run is a measurement too.
        forbidden = 'test x11 = \\"x$MISE_OFFLINE$MISE_LOCKED\\"'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(
                root,
                constitution_text=constitution(
                    execution="default",
                    quick_task=MISE_TASK,
                    provider_name="mise",
                    sensor_task=MISE_SENSOR_TASK,
                    full_command=forbidden,
                ),
            )
            log = root / "mise.log"

            result = self._run(case, log)

            self.assertEqual("ACCEPTED", result["status"])
            data_dir = self._data_dir(case)
            empty = str(data_dir / "config" / "empty.toml")
            measured = [c for c in self._calls(log) if c["args"][0] == "run"]
            self.assertTrue(measured)
            trees = {result["repo"], result["worktree"]}
            for call in measured:
                variables = call["env"]
                self.assertEqual(set(MISE_VARIABLES), set(variables))
                self.assertEqual(
                    {"MISE_OFFLINE": "1", "MISE_LOCKED": "1"}
                    | dict.fromkeys(FORBIDDING[2:], "false"),
                    {name: variables[name] for name in FORBIDDING},
                )
                self.assertEqual(str(data_dir), variables["MISE_DATA_DIR"])
                self.assertEqual(
                    "mise.toml", variables["MISE_OVERRIDE_CONFIG_FILENAMES"]
                )
                self.assertEqual(empty, variables["MISE_GLOBAL_CONFIG_FILE"])
                self.assertEqual(empty, variables["MISE_SYSTEM_CONFIG_FILE"])
                # One tree is trusted and read, and the search stops above it.
                trusted = variables["MISE_TRUSTED_CONFIG_PATHS"]
                self.assertIn(trusted, trees)
                self.assertEqual(
                    str(Path(trusted).parent), variables["MISE_CEILING_PATHS"]
                )
            # The installation is not a measurement: locked, and not offline.
            [installed] = [c for c in self._calls(log) if c["args"][0] == "install"]
            self.assertEqual("1", installed["env"]["MISE_LOCKED"])
            self.assertNotIn("MISE_OFFLINE", installed["env"])

    def test_a_candidate_writing_the_local_configuration_is_a_control_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(
                root,
                implementer="""
                implement(ACCEPTABLE)
                (worktree / "mise.local.toml").write_text('[tools]\\nfake-tool = "2"\\n')
                """,
            )
            log = root / "mise.log"

            result = self._run(case, log)

            self.assertEqual("REJECTED", result["status"])
            self.assertEqual(
                ["execution environment: mise.local.toml changed during the run"],
                result["decision"]["reasons"],
            )
            self.assertFalse(result["environment"]["candidate"]["unchanged_at_end"])

    def test_each_confinement_names_the_controllers_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            log = root / "mise.log"

            result = self._run(case, log)

            self.assertEqual("ACCEPTED", result["status"])
            repo = Path(result["repo"])
            worktree = Path(result["worktree"])
            data_dir = str(self._data_dir(case))
            source = result["gate_isolation"]["source"]
            candidate = result["gate_isolation"]["candidate"]
            # A gate reads the metadata of the tree it measures and the
            # toolchain every measurement runs on, and writes neither.
            self.assertEqual(
                [result["run_dir"], str(repo / ".git"), data_dir],
                source["read_only_paths"],
            )
            self.assertEqual(
                [result["run_dir"], str(worktree / ".git"), data_dir],
                candidate["read_only_paths"],
            )
            # So does the actuator: it runs the tools and cannot change them.
            actuator = result["actuator_isolation"]
            self.assertEqual(
                [str(repo), str(worktree / ".git"), data_dir],
                actuator["read_only_paths"],
            )
            self.assertNotIn(data_dir, actuator["denied_paths"])


if __name__ == "__main__":
    unittest.main()
