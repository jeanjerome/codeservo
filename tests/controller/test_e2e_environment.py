"""Measuring through an environment the lockfile pins."""

import json
import tempfile
import unittest
from pathlib import Path

from codeservo.evidence.digests import sha256_file

from harness import (
    COMPILE_COMMAND,
    Case,
    PIXI_PACKAGES,
    PIXI_TASK,
    build_case,
    constitution,
)


class ExecutionEnvironmentE2ETests(unittest.TestCase):
    """A run that measures through a declared execution environment."""

    def _case(
        self,
        root: Path,
        *,
        task: str = PIXI_TASK,
        implementer: str = "implement(ACCEPTABLE)",
        constitution_text: str | None = None,
        **overrides,
    ) -> Case:
        return build_case(
            root,
            implementer=implementer,
            provider=True,
            constitution_text=(
                constitution(execution="default", quick_task=task)
                if constitution_text is None
                else constitution_text
            ),
            **overrides,
        )

    def _run(
        self, case: Case, log: Path, *, env: dict[str, str] | None = None, **overrides
    ) -> dict:
        return case.run(
            env={"CODESERVO_TEST_PIXI_LOG": str(log), **(env or {})}, **overrides
        )

    def _calls(self, log: Path) -> list[dict]:
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    def _invocations(self, log: Path) -> list[list[str]]:
        return [call["args"] for call in self._calls(log)]

    def _subcommands(self, log: Path) -> list[str]:
        return [call[0] for call in self._invocations(log)]

    def test_freezes_the_environment_and_measures_through_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            log = root / "pixi.log"

            result = self._run(case, log)

            self.assertEqual("ACCEPTED", result["status"])
            evidence = json.loads(Path(result["run_dir"], "evidence.json").read_text())
            environment = evidence["environment"]
            self.assertEqual("pixi", environment["provider"])
            self.assertEqual("0.77.1-test", environment["provider_version"])
            self.assertEqual("pyproject.toml", environment["manifest_path"])
            self.assertEqual("pixi.lock", environment["lock_path"])
            self.assertEqual("default", environment["environment"])
            self.assertEqual("test-platform", environment["platform"])
            self.assertEqual([PIXI_TASK], environment["declared_tasks"])
            # The digests are of the source repository at the base commit.
            self.assertEqual(
                sha256_file(case.repo / "pyproject.toml"),
                environment["manifest_sha256"],
            )
            self.assertEqual(
                sha256_file(case.repo / "pixi.lock"), environment["lock_sha256"]
            )
            stored = Path(result["run_dir"], environment["packages_path"])
            self.assertEqual("environment/packages.json", environment["packages_path"])
            self.assertEqual(PIXI_PACKAGES, json.loads(stored.read_text()))
            self.assertEqual(sha256_file(stored), environment["packages_sha256"])
            self.assertEqual(len(PIXI_PACKAGES), environment["package_count"])
            # Nothing the description says about the operator is recorded.
            for private in ("/operator", "cache_dir", "auth_dir", "config_locations"):
                self.assertNotIn(
                    private, Path(result["run_dir"], "evidence.json").read_text()
                )

    def test_each_gate_names_the_manifest_of_the_tree_it_measures(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            log = root / "pixi.log"

            result = self._run(case, log)

            def command(name: str, gates: list[dict]) -> str:
                return next(gate["command"] for gate in gates if gate["name"] == name)

            worktree = Path(result["worktree"])
            repo = Path(result["repo"])
            baseline = command("syntax", result["baseline"])
            quick = command("syntax", result["iterations"][-1]["quick_gates"])
            self.assertEqual(
                "pixi run --as-is --clean-env --no-config"
                f" --manifest-path '{repo / 'pyproject.toml'}'"
                f" --environment 'default' '{PIXI_TASK}'",
                baseline,
            )
            self.assertEqual(
                "pixi run --as-is --clean-env --no-config"
                f" --manifest-path '{worktree / 'pyproject.toml'}'"
                f" --environment 'default' '{PIXI_TASK}'",
                quick,
            )
            self.assertNotIn(str(worktree), baseline)
            self.assertNotIn(str(repo), quick)
            # A shell gate is built and run exactly as it is without a provider.
            self.assertEqual(
                COMPILE_COMMAND, command("full", result["full_gates"])
            )
            self.assertNotIn(
                "pixi",
                command("task-outcome", result["iterations"][-1]["quick_gates"]),
            )

    def test_never_asks_the_provider_to_write_the_lockfile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            log = root / "pixi.log"

            self._run(case, log)

            invocations = self._invocations(log)
            self.assertTrue(invocations)
            # The description is asked twice: once of the source, once of the
            # candidate whose directory the installation reports.
            self.assertEqual(
                ["list", "info", "info", "install"],
                [call[0] for call in invocations if call[0] != "run"],
            )

    def test_refuses_a_lockfile_that_disagrees_with_the_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root, stale_lock=True)
            log = root / "pixi.log"
            manifest = sha256_file(case.repo / "pyproject.toml")
            lock = sha256_file(case.repo / "pixi.lock")

            result = self._run(case, log)

            self.assertEqual("REJECTED", result["status"])
            self.assertEqual(1, len(result["decision"]["reasons"]))
            self.assertIn("pixi.lock", result["decision"]["reasons"][0])
            # Before the baseline, and before any checkout.
            self.assertNotIn("baseline", result)
            self.assertIsNone(result["worktree"])
            self.assertEqual([], result["iterations"])
            # The frozen control input is byte-identical afterwards.
            self.assertEqual(manifest, sha256_file(case.repo / "pyproject.toml"))
            self.assertEqual(lock, sha256_file(case.repo / "pixi.lock"))
            self.assertEqual(["list"], self._subcommands(log))
            environment = result["environment"]
            self.assertEqual(manifest, environment["manifest_sha256"])
            self.assertNotIn("packages_path", environment)

    def test_refuses_a_task_the_environment_does_not_declare(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root, task="absent-task")
            log = root / "pixi.log"

            result = self._run(case, log)

            self.assertEqual("REJECTED", result["status"])
            self.assertIn("absent-task", result["decision"]["reasons"][0])
            self.assertNotIn("baseline", result)
            self.assertIsNone(result["worktree"])
            # No provider task ever ran, and nothing was installed.
            self.assertEqual(["list", "info"], self._subcommands(log))

    def test_installs_the_candidate_after_the_checkout_and_before_the_agent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(
                root,
                implementer="""
                (worktree / "prepared.txt").write_text(
                    "yes"
                    if (worktree / ".pixi" / "envs" / "default").is_dir()
                    else "no"
                )
                implement(ACCEPTABLE)
                """,
            )
            log = root / "pixi.log"

            result = self._run(case, log)

            self.assertEqual("ACCEPTED", result["status"])
            worktree = Path(result["worktree"])
            # The environment existed before the actuator was ever started.
            self.assertEqual(
                "yes", (worktree / "prepared.txt").read_text(encoding="utf-8")
            )
            install = next(
                call for call in self._invocations(log) if call[0] == "install"
            )
            # Into the checkout, and never into the source repository.
            self.assertEqual(1, self._subcommands(log).count("install"))
            self.assertIn(str(worktree / "pyproject.toml"), install)
            self.assertNotIn(str(Path(result["repo"]) / "pyproject.toml"), install)
            # The operator's environment is left exactly as it was found.
            source_prefix = Path(result["repo"], ".pixi", "envs", "default")
            self.assertEqual([], list(source_prefix.iterdir()))
            subcommands = self._subcommands(log)
            # After the checkout: the baseline already measured the source.
            self.assertLess(subcommands.index("run"), subcommands.index("install"))

    def test_records_what_the_installation_did(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            log = root / "pixi.log"

            result = self._run(case, log)

            worktree = Path(result["worktree"])
            candidate = result["environment"]["candidate"]
            self.assertEqual(
                {
                    "prefix_path",
                    "command",
                    "exit_code",
                    "duration_ms",
                    "manifest_sha256",
                    "lock_sha256",
                    "config_sha256",
                    "unchanged_at_end",
                },
                set(candidate),
            )
            self.assertEqual(
                [
                    "pixi",
                    "install",
                    "--locked",
                    "--no-config",
                    "--environment",
                    "default",
                    "--manifest-path",
                    str(worktree / "pyproject.toml"),
                ],
                candidate["command"],
            )
            self.assertEqual(0, candidate["exit_code"])
            self.assertGreaterEqual(candidate["duration_ms"], 0)
            self.assertEqual(
                str(worktree / ".pixi" / "envs" / "default"), candidate["prefix_path"]
            )
            self.assertTrue(Path(candidate["prefix_path"]).is_dir())
            # The digests are of the candidate, and the workspace never moved.
            self.assertEqual(
                sha256_file(worktree / "pyproject.toml"), candidate["manifest_sha256"]
            )
            self.assertEqual(
                sha256_file(worktree / "pixi.lock"), candidate["lock_sha256"]
            )
            self.assertIsNone(candidate["config_sha256"])
            self.assertTrue(candidate["unchanged_at_end"])
            evidence = json.loads(Path(result["run_dir"], "evidence.json").read_text())
            self.assertFalse(
                Path(evidence["environment"]["candidate"]["prefix_path"]).is_absolute()
            )

    def test_a_refused_installation_ends_the_run_before_any_actuation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            log = root / "pixi.log"

            result = self._run(
                case, log, env={"CODESERVO_TEST_PIXI_INSTALL_FAILS": "1"}
            )

            self.assertEqual("REJECTED", result["status"])
            self.assertEqual(1, len(result["decision"]["reasons"]))
            reason = result["decision"]["reasons"][0]
            self.assertIn("execution environment", reason)
            self.assertIn("default", reason)
            # Nothing actuated, and no measurement ran in the candidate.
            self.assertEqual([], result["iterations"])
            self.assertNotIn("full_gates", result)
            candidate = result["environment"]["candidate"]
            self.assertEqual(1, candidate["exit_code"])
            self.assertFalse(Path(candidate["prefix_path"]).exists())
            self.assertEqual(
                ["list", "info", "run", "info", "install"],
                self._subcommands(log),
            )

    def test_refuses_a_source_repository_without_the_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root, source_environment=False)
            log = root / "pixi.log"

            result = self._run(case, log)

            self.assertEqual("REJECTED", result["status"])
            reason = result["decision"]["reasons"][0]
            self.assertIn("environment default is not installed", reason)
            self.assertIn(str(case.repo / ".pixi" / "envs" / "default"), reason)
            # Before the baseline gates, and before any checkout.
            self.assertNotIn("baseline", result)
            self.assertIsNone(result["worktree"])
            self.assertEqual(["list", "info"], self._subcommands(log))
            # The controller wrote nothing into the operator's tree.
            self.assertFalse((case.repo / ".pixi").exists())

    def test_no_measurement_can_resolve_or_install(self) -> None:
        forbidden = (
            "test xtruetruetrue = \\\"x$PIXI_OFFLINE$PIXI_NO_INSTALL$PIXI_FROZEN\\\""
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(
                root,
                constitution_text=constitution(
                    execution="default",
                    quick_task=PIXI_TASK,
                    full_command=forbidden,
                    sensor_command=None,
                ),
            )
            log = root / "pixi.log"

            result = self._run(case, log)

            # A shell gate of a provider run is a measurement too.
            self.assertEqual("ACCEPTED", result["status"])
            measured = [call for call in self._calls(log) if call["args"][0] == "run"]
            self.assertTrue(measured)
            for call in measured:
                self.assertEqual(
                    {
                        "PIXI_OFFLINE": "true",
                        "PIXI_NO_INSTALL": "true",
                        "PIXI_FROZEN": "true",
                    },
                    call["env"],
                )
            installed = [
                call for call in self._calls(log) if call["args"][0] == "install"
            ]
            # The installation is not a measurement: none of the three reaches
            # it, or it would install nothing and still report success.
            self.assertEqual([{}], [call["env"] for call in installed])

    def test_a_candidate_file_that_changed_is_a_control_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(
                root,
                implementer="""
                implement(ACCEPTABLE)
                (worktree / "pixi.lock").write_text(
                    "version: 6\\nenvironments: {}\\n# resolved again\\n"
                )
                """,
            )
            log = root / "pixi.log"

            result = self._run(case, log)

            self.assertEqual("REJECTED", result["status"])
            self.assertEqual(
                ["execution environment: pixi.lock changed during the run"],
                result["decision"]["reasons"],
            )
            # A control failure and not a failing gate: every gate passed.
            iteration = result["iterations"][-1]
            self.assertTrue(all(g["passed"] for g in iteration["quick_gates"]))
            self.assertTrue(iteration["scope"]["passed"])
            self.assertNotIn("full_gates", result)
            self.assertFalse(result["environment"]["candidate"]["unchanged_at_end"])

    def test_the_environment_directory_is_never_a_candidate_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            log = root / "pixi.log"

            result = self._run(case, log)

            self.assertEqual("ACCEPTED", result["status"])
            worktree = Path(result["worktree"])
            self.assertTrue((worktree / ".pixi" / "envs" / "default").is_dir())
            scope = result["iterations"][-1]["scope"]
            self.assertEqual(["app.py"], scope["details"]["changed_files"])
            self.assertEqual([], scope["details"]["violations"])
            self.assertEqual(2, scope["details"]["diff_lines"])
            patch_text = Path(result["run_dir"], "change.patch").read_text()
            observed = Path(
                result["run_dir"], result["iterations"][-1]["observed_state"]["path"]
            ).read_text()
            for text in (patch_text, observed):
                self.assertNotIn(".pixi", text)

    def test_each_confinement_names_the_tree_it_measures(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            log = root / "pixi.log"

            result = self._run(case, log)

            self.assertEqual("ACCEPTED", result["status"])
            repo = Path(result["repo"])
            worktree = Path(result["worktree"])
            source = result["gate_isolation"]["source"]
            candidate = result["gate_isolation"]["candidate"]
            # A gate reads the metadata and the environment of the tree it
            # measures, writes neither, and is never handed the other tree.
            self.assertEqual(
                [result["run_dir"], str(repo / ".git"), str(repo / ".pixi")],
                source["read_only_paths"],
            )
            self.assertEqual(
                [result["run_dir"], str(worktree / ".git"), str(worktree / ".pixi")],
                candidate["read_only_paths"],
            )
            for document in (source, candidate):
                self.assertEqual("macos-sandbox-exec", document["mechanism"])
                self.assertEqual([], document["denied_paths"])
            self.assertFalse(
                [path for path in source["read_only_paths"][1:] if str(worktree) in path]
            )
            self.assertFalse(
                [path for path in candidate["read_only_paths"][1:] if str(repo) in path]
            )
            # The actuator reads both, and writes neither.
            actuator = result["actuator_isolation"]
            self.assertEqual(
                [str(repo), str(worktree / ".git"), str(worktree / ".pixi")],
                actuator["read_only_paths"],
            )
            for protected in (worktree / ".git", worktree / ".pixi"):
                self.assertNotIn(str(protected), actuator["denied_paths"])
            self.assertTrue((worktree / ".pixi").is_dir())
            # The reviewer's confinement is unchanged: the whole worktree.
            self.assertEqual(
                [str(repo), str(worktree)],
                result["review"]["isolation"]["read_only_paths"],
            )

    def test_a_run_without_a_provider_never_invokes_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = build_case(
                root, implementer="implement(ACCEPTABLE)", provider=True
            )
            log = root / "pixi.log"

            result = self._run(case, log)

            self.assertEqual("ACCEPTED", result["status"])
            self.assertEqual({"provider": "none"}, result["environment"])
            self.assertEqual([], self._calls(log))
            self.assertNotIn("candidate", result["environment"])
            self.assertFalse((Path(result["worktree"]) / ".pixi").exists())


if __name__ == "__main__":
    unittest.main()
