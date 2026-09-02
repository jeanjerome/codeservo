"""What the controller asks the provider, and what it refuses to measure."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeservo.workspace import pixi
from harness import PIXI_PACKAGES, PIXI_TASK, write_provider


class ProviderCommandTests(unittest.TestCase):
    """The option set is the controller's, and the values are quoted."""

    def test_reads_the_verdict_and_the_inventory_in_one_command(self) -> None:
        self.assertEqual(
            [
                "pixi",
                "list",
                "--json",
                "--locked",
                "--no-install",
                "--no-config",
                "--manifest-path",
                "/tree/pyproject.toml",
            ],
            pixi.inventory_command(Path("/tree/pyproject.toml")),
        )

    def test_never_asks_the_provider_to_write_the_lockfile(self) -> None:
        commands = [
            pixi.inventory_command(Path("/tree/pyproject.toml")),
            pixi.description_command(Path("/tree/pyproject.toml")),
            pixi.install_command(
                manifest=Path("/tree/pyproject.toml"), environment="default"
            ),
            pixi.task_command(
                manifest=Path("/tree/pyproject.toml"),
                environment="default",
                task="unit",
            ).split(),
        ]

        self.assertTrue(all("lock" not in command for command in commands))

    def test_installs_from_the_lockfile_and_resolves_nothing(self) -> None:
        self.assertEqual(
            [
                "pixi",
                "install",
                "--locked",
                "--no-config",
                "--environment",
                "default",
                "--manifest-path",
                "/tree/pyproject.toml",
            ],
            pixi.install_command(
                manifest=Path("/tree/pyproject.toml"), environment="default"
            ),
        )

    def test_names_the_workspace_configuration_of_a_manifest(self) -> None:
        self.assertEqual(
            Path("/tree/.pixi/config.toml"),
            pixi.config_path(Path("/tree/pyproject.toml")),
        )
        self.assertEqual(
            Path("/tree/sub/.pixi/config.toml"),
            pixi.config_path(Path("/tree/sub/pyproject.toml")),
        )

    def test_names_the_directory_the_provider_owns_in_a_workspace(self) -> None:
        # The environments and the workspace configuration both live under it,
        # so a confinement protects the directory and not either file.
        self.assertEqual(
            Path("/tree/.pixi"),
            pixi.provider_directory(Path("/tree/pyproject.toml")),
        )
        self.assertEqual(
            Path("/tree/sub/.pixi"),
            pixi.provider_directory(Path("/tree/sub/pyproject.toml")),
        )
        self.assertEqual(
            pixi.config_path(Path("/tree/pyproject.toml")).parent,
            pixi.provider_directory(Path("/tree/pyproject.toml")),
        )

    def test_a_measurement_can_neither_resolve_nor_install(self) -> None:
        self.assertEqual(
            {
                "PIXI_OFFLINE": "true",
                "PIXI_NO_INSTALL": "true",
                "PIXI_FROZEN": "true",
            },
            pixi.measurement_environment(),
        )
        # A caller that edits what it was handed cannot change the next one.
        pixi.measurement_environment()["PIXI_FROZEN"] = "false"
        self.assertEqual("true", pixi.measurement_environment()["PIXI_FROZEN"])

    def test_describes_without_reading_configuration(self) -> None:
        self.assertEqual(
            [
                "pixi",
                "info",
                "--json",
                "--no-config",
                "--manifest-path",
                "/tree/pyproject.toml",
            ],
            pixi.description_command(Path("/tree/pyproject.toml")),
        )

    def test_builds_a_task_command_of_exactly_the_declared_options(self) -> None:
        self.assertEqual(
            "pixi run --as-is --clean-env --no-config"
            " --manifest-path '/tree/pyproject.toml'"
            " --environment 'default' 'test-unit'",
            pixi.task_command(
                manifest=Path("/tree/pyproject.toml"),
                environment="default",
                task="test-unit",
            ),
        )

    def test_passes_arguments_to_the_task_after_its_name(self) -> None:
        """The one channel into a task: `--clean-env` leaves it no other."""
        self.assertEqual(
            "pixi run --as-is --clean-env --no-config"
            " --manifest-path '/tree/pyproject.toml'"
            " --environment 'default' 'coverage' '/owned/observation.json'",
            pixi.task_command(
                manifest=Path("/tree/pyproject.toml"),
                environment="default",
                task="coverage",
                arguments=("/owned/observation.json",),
            ),
        )

    def test_quotes_an_argument_the_controller_supplies(self) -> None:
        command = pixi.task_command(
            manifest=Path("/tree/pyproject.toml"),
            environment="default",
            task="coverage",
            arguments=("/owned dir/observation.json",),
        )

        self.assertTrue(command.endswith("'/owned dir/observation.json'"))

    def test_quotes_every_value_the_constitution_supplies(self) -> None:
        command = pixi.task_command(
            manifest=Path("/tree/a b/pyproject.toml"),
            environment="e n v",
            task="rm -rf /",
        )

        self.assertIn("'/tree/a b/pyproject.toml'", command)
        self.assertIn("'e n v'", command)
        self.assertTrue(command.endswith("'rm -rf /'"))

    def test_quotes_a_value_carrying_a_quote(self) -> None:
        command = pixi.task_command(
            manifest=Path("/tree/pyproject.toml"),
            environment="default",
            task="a'; touch pwned; '",
        )

        self.assertTrue(command.endswith("""'a'\\''; touch pwned; '\\'''"""))


class ProviderInstallTests(unittest.TestCase):
    """Making one environment exist, and refusing to pretend it does."""

    def _workspace(self, **overrides) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        repo = root / "repo"
        bin_dir = root / "bin"
        repo.mkdir()
        bin_dir.mkdir()
        write_provider(bin_dir, repo, installed=False, **overrides)
        path = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
        patcher = patch.dict(os.environ, {"PATH": path}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        return repo

    def _install(self, repo: Path, environment: str = "default") -> pixi.Installation:
        return pixi.install(
            manifest=repo / "pyproject.toml", environment=environment
        )

    def test_creates_the_directory_the_provider_reports(self) -> None:
        repo = self._workspace()
        prefix = repo / ".pixi" / "envs" / "default"
        self.assertFalse(prefix.exists())

        installed = self._install(repo)

        self.assertEqual(str(prefix), installed.prefix_path)
        self.assertTrue(prefix.is_dir())
        self.assertEqual(0, installed.exit_code)
        self.assertGreaterEqual(installed.duration_ms, 0)
        self.assertEqual(
            (
                "pixi",
                "install",
                "--locked",
                "--no-config",
                "--environment",
                "default",
                "--manifest-path",
                str(repo / "pyproject.toml"),
            ),
            installed.command,
        )

    def test_reads_the_directory_from_the_provider_and_not_from_the_manifest(
        self,
    ) -> None:
        repo = self._workspace()

        self.assertEqual(
            str(repo / ".pixi" / "envs" / "gates"),
            pixi.environment_prefix(
                manifest=repo / "pyproject.toml", environment="gates"
            ),
        )

    def test_installs_without_the_variables_that_would_make_it_a_no_op(self) -> None:
        """The three set would install nothing and still report success."""
        repo = self._workspace()

        with patch.dict(
            os.environ,
            {
                "PIXI_OFFLINE": "true",
                "PIXI_NO_INSTALL": "true",
                "PIXI_FROZEN": "true",
            },
            clear=False,
        ):
            installed = self._install(repo)

        self.assertEqual(0, installed.exit_code)
        self.assertTrue(Path(installed.prefix_path).is_dir())

    def test_reports_an_installation_the_provider_refused(self) -> None:
        repo = self._workspace()

        with patch.dict(
            os.environ, {"CODESERVO_TEST_PIXI_INSTALL_FAILS": "1"}, clear=False
        ):
            installed = self._install(repo)

        self.assertEqual(1, installed.exit_code)
        self.assertIn("failed to install default", installed.diagnostic)
        # The directory it was refused for is still the one it reported.
        self.assertEqual(str(repo / ".pixi" / "envs" / "default"), installed.prefix_path)
        self.assertFalse(Path(installed.prefix_path).exists())

    def test_a_disagreeing_lockfile_installs_nothing(self) -> None:
        repo = self._workspace(stale_lock=True)
        before = (repo / "pixi.lock").read_bytes()

        installed = self._install(repo)

        self.assertEqual(1, installed.exit_code)
        self.assertIn("lock file not up-to-date", installed.diagnostic)
        self.assertFalse(Path(installed.prefix_path).exists())
        # It refuses without rewriting what it refuses.
        self.assertEqual(before, (repo / "pixi.lock").read_bytes())


class ProviderFreezeTests(unittest.TestCase):
    """One resolved environment, or a refusal to measure through it."""

    def _workspace(self, *, stale_lock: bool = False) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        repo = root / "repo"
        bin_dir = root / "bin"
        repo.mkdir()
        bin_dir.mkdir()
        write_provider(bin_dir, repo, stale_lock=stale_lock)
        path = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
        patcher = patch.dict(os.environ, {"PATH": path}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        return repo

    def _freeze(self, repo: Path, **overrides) -> pixi.Environment:
        arguments = {
            "manifest": repo / "pyproject.toml",
            "lock_path": "pixi.lock",
            "environment": "default",
            "tasks": (PIXI_TASK,),
        }
        arguments.update(overrides)
        return pixi.freeze(**arguments)

    def test_resolves_the_inventory_the_lockfile_pins(self) -> None:
        repo = self._workspace()

        resolved = self._freeze(repo)

        self.assertEqual(str(repo / ".pixi" / "envs" / "default"), resolved.prefix)
        self.assertEqual("0.77.1-test", resolved.version)
        self.assertEqual("test-platform", resolved.platform)
        self.assertEqual((PIXI_TASK,), resolved.tasks)
        self.assertEqual(PIXI_PACKAGES, resolved.packages)

    def test_reads_the_tasks_of_the_selected_environment(self) -> None:
        repo = self._workspace()

        resolved = self._freeze(repo, environment="gates", tasks=("extra-check",))

        self.assertEqual(("check-syntax", "extra-check"), resolved.tasks)

    def test_refuses_a_lockfile_that_disagrees_with_the_manifest(self) -> None:
        repo = self._workspace(stale_lock=True)
        before = (repo / "pixi.lock").read_bytes()

        with self.assertRaisesRegex(pixi.ProviderError, "pixi.lock"):
            self._freeze(repo)

        # The verdict is read from a command that writes neither file.
        self.assertEqual(before, (repo / "pixi.lock").read_bytes())

    def test_refuses_an_environment_the_provider_does_not_declare(self) -> None:
        repo = self._workspace()

        with self.assertRaisesRegex(pixi.ProviderError, "no environment absent"):
            self._freeze(repo, environment="absent")

    def test_refuses_a_task_the_environment_does_not_declare(self) -> None:
        repo = self._workspace()

        with self.assertRaisesRegex(pixi.ProviderError, "no task extra-check"):
            self._freeze(repo, tasks=(PIXI_TASK, "extra-check"))

    def test_refuses_a_provider_that_cannot_be_run(self) -> None:
        repo = self._workspace()
        with patch.dict(os.environ, {"PATH": "/nonexistent"}, clear=False):
            with self.assertRaisesRegex(pixi.ProviderError, "cannot run pixi list"):
                self._freeze(repo)

    def test_a_description_that_fails_is_still_read(self) -> None:
        """The description is never a verdict: its exit status is not read."""
        repo = self._workspace()
        # A provider whose description exits non-zero while still describing.
        (repo.parent / "bin" / "pixi").write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "args = sys.argv[1:]\n"
            "if args[0] == 'list':\n"
            f"    json.dump({PIXI_PACKAGES!r}, sys.stdout)\n"
            "    raise SystemExit(0)\n"
            "json.dump(\n"
            "    {\n"
            "        'version': '0.77.1-test',\n"
            "        'platform': 'test-platform',\n"
            "        'environments_info': ["
            f"{{'name': 'default', 'tasks': ['{PIXI_TASK}'],"
            " 'prefix': '/tree/.pixi/envs/default'}],\n"
            "    },\n"
            "    sys.stdout,\n"
            ")\n"
            "raise SystemExit(1)\n",
            encoding="utf-8",
        )

        resolved = self._freeze(repo)

        self.assertEqual((PIXI_TASK,), resolved.tasks)

    def test_reads_nothing_the_description_says_about_the_operator(self) -> None:
        repo = self._workspace()

        resolved = self._freeze(repo)

        serialized = json.dumps(
            {
                "version": resolved.version,
                "platform": resolved.platform,
                "tasks": list(resolved.tasks),
                "packages": resolved.packages,
            }
        )
        for private in ("/operator", "cache_dir", "auth_dir", "config_locations"):
            self.assertNotIn(private, serialized)


class DescriptionReadingTests(unittest.TestCase):
    """What the provider printed, projected onto what a record carries.

    The description is another program's output. Every shape below parses as
    JSON and is not the shape the reader was written for, so each one is a way
    the run could have ended in a traceback rather than in a refusal.
    """

    def read(self, document: object) -> pixi.Description:
        return pixi.read_description(
            json.dumps(document), manifest_name="pyproject.toml", environment="default"
        )

    def described(self, **environment: object) -> dict:
        return {
            "version": "0.77.1",
            "platform": "osx-arm64",
            "environments_info": [
                {
                    "name": "default",
                    "tasks": ["lint", "test"],
                    "prefix": "/tree/.pixi/envs/default",
                    **environment,
                }
            ],
        }

    def test_reads_the_four_facts_a_run_measures_through(self) -> None:
        described = self.read(self.described())

        self.assertEqual("0.77.1", described.version)
        self.assertEqual("osx-arm64", described.platform)
        self.assertEqual(("lint", "test"), described.tasks)
        self.assertEqual("/tree/.pixi/envs/default", described.prefix)

    def test_refuses_a_description_that_is_not_an_object(self) -> None:
        for document in ([], "0.77.1", 1, None):
            with self.subTest(document=document):
                with self.assertRaisesRegex(
                    pixi.ProviderError, "description is not an object"
                ):
                    self.read(document)

    def test_refuses_an_environment_list_that_is_not_a_list(self) -> None:
        with self.assertRaisesRegex(pixi.ProviderError, "no list of environments"):
            self.read({"environments_info": {"default": {}}})

    def test_refuses_a_task_set_that_is_not_a_list(self) -> None:
        with self.assertRaisesRegex(pixi.ProviderError, "no task set"):
            self.read(self.described(tasks="lint"))

    def test_refuses_an_environment_reporting_no_directory(self) -> None:
        for prefix in ("", "   ", None, {"path": "/tree"}):
            with self.subTest(prefix=prefix):
                with self.assertRaisesRegex(pixi.ProviderError, "no directory"):
                    self.read(self.described(prefix=prefix))

    def test_reports_a_self_description_the_provider_did_not_make(self) -> None:
        """`str()` of a mapping would state something the provider never said."""
        described = self.read(
            {**self.described(), "version": {"pixi": "0.77.1"}, "platform": None}
        )

        self.assertEqual(pixi.UNREPORTED, described.version)
        self.assertEqual(pixi.UNREPORTED, described.platform)

    def test_keeps_only_the_tasks_named_as_strings(self) -> None:
        described = self.read(self.described(tasks=["test", 1, None, "lint"]))

        self.assertEqual(("lint", "test"), described.tasks)


if __name__ == "__main__":
    unittest.main()
