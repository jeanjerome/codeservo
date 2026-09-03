"""What the controller asks mise, what it forbids it, and what it refuses."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeservo.workspace import mise
from codeservo.workspace.provider import ProviderError
from harness import (
    MISE_SENSOR_TASK,
    MISE_TASK,
    MISE_TOOL,
    MISE_TOOL_VERSION,
    mise_lock,
    mise_manifest,
    write_mise_provider,
)

MANIFEST = Path("/tree/mise.toml")
DATA_DIR = Path("/state/providers/mise")


class ProviderCommandTests(unittest.TestCase):
    """The option set is the controller's, and every value is quoted."""

    def setUp(self) -> None:
        self.provider = mise.Mise(DATA_DIR)

    def test_asks_by_the_directory_holding_the_manifest(self) -> None:
        self.assertEqual(["mise", "version", "--json"], self.provider.version_command())
        self.assertEqual(
            ["mise", "tasks", "ls", "--json", "-C", "/tree"],
            self.provider.tasks_command(MANIFEST),
        )
        self.assertEqual(
            ["mise", "ls", "--json", "--current", "-C", "/tree"],
            self.provider.inventory_command(MANIFEST),
        )
        self.assertEqual(
            ["mise", "install", "-C", "/tree"], self.provider.install_command(MANIFEST)
        )

    def test_never_asks_the_provider_to_write_the_lockfile(self) -> None:
        commands = [
            self.provider.version_command(),
            self.provider.tasks_command(MANIFEST),
            self.provider.inventory_command(MANIFEST),
            self.provider.install_command(MANIFEST),
            self.provider.task_command(
                manifest=MANIFEST, environment="default", task="unit"
            ).split(),
        ]

        self.assertTrue(all("lock" not in command for command in commands))

    def test_builds_a_task_command_of_exactly_the_declared_options(self) -> None:
        self.assertEqual(
            "mise run -q -C '/tree' 'unit'",
            self.provider.task_command(
                manifest=MANIFEST, environment="default", task="unit"
            ),
        )
        # Arguments follow the separator, where mise hands them to the task.
        self.assertEqual(
            "mise run -q -C '/tree' 'unit' -- '/run/observation.json'",
            self.provider.task_command(
                manifest=MANIFEST,
                environment="default",
                task="unit",
                arguments=["/run/observation.json"],
            ),
        )

    def test_quotes_what_the_shell_would_otherwise_read(self) -> None:
        command = self.provider.task_command(
            manifest=Path("/tree with space/it's/mise.toml"),
            environment="default",
            task="a task",
            arguments=["$HOME"],
        )

        self.assertEqual(
            "mise run -q -C '/tree with space/it'\\''s' 'a task' -- '$HOME'", command
        )

    def test_refuses_any_environment_but_the_default(self) -> None:
        calls = {
            "task": lambda: self.provider.task_command(
                manifest=MANIFEST, environment="prod", task="unit"
            ),
            "install": lambda: self.provider.install(
                manifest=MANIFEST, environment="prod"
            ),
            # Refused before the manifest is read: none exists at that path.
            "freeze": lambda: self.provider.freeze(
                manifest=MANIFEST, lock_path="mise.lock", environment="prod", tasks=()
            ),
        }

        for name, call in calls.items():
            with self.subTest(name):
                with self.assertRaisesRegex(
                    ProviderError, "mise declares no environment prod"
                ):
                    call()

    def test_names_the_configuration_that_would_override_the_manifest(self) -> None:
        self.assertEqual(
            Path("/tree/mise.local.toml"), self.provider.config_path(MANIFEST)
        )
        self.assertEqual(
            Path("/tree/sub/.mise.local.toml"),
            self.provider.config_path(Path("/tree/sub/.mise.toml")),
        )

    def test_the_directory_the_provider_owns_is_the_controllers(self) -> None:
        for manifest in (MANIFEST, Path("/elsewhere/sub/mise.toml")):
            self.assertEqual(DATA_DIR, self.provider.provider_directory(manifest))
            self.assertFalse(
                self.provider.provider_directory(manifest).is_relative_to(
                    manifest.parent
                )
            )
        self.assertTrue(self.provider.shared_installs)
        self.assertEqual("mise.lock", self.provider.lockfile)


class MeasurementEnvironmentTests(unittest.TestCase):
    """What every measurement runs under: one manifest, one directory, no network."""

    def test_a_measurement_can_neither_resolve_install_nor_read_the_operator(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "providers" / "mise"
            provider = mise.Mise(data_dir)
            empty = data_dir / "config" / "empty.toml"

            variables = provider.measurement_environment(MANIFEST)

            self.assertEqual(
                {
                    "MISE_DATA_DIR": str(data_dir),
                    "MISE_CACHE_DIR": str(data_dir / "cache"),
                    "MISE_GLOBAL_CONFIG_FILE": str(empty),
                    "MISE_SYSTEM_CONFIG_FILE": str(empty),
                    "MISE_OVERRIDE_CONFIG_FILENAMES": "mise.toml",
                    "MISE_CEILING_PATHS": "/",
                    "MISE_TRUSTED_CONFIG_PATHS": "/tree",
                    "MISE_YES": "1",
                    "MISE_LOCKED": "1",
                    "MISE_OFFLINE": "1",
                    "MISE_AUTO_INSTALL": "false",
                    "MISE_EXEC_AUTO_INSTALL": "false",
                    "MISE_NOT_FOUND_AUTO_INSTALL": "false",
                    "MISE_TASK_RUN_AUTO_INSTALL": "false",
                },
                variables,
            )
            # The file standing in for the operator's configuration says nothing.
            self.assertEqual("", empty.read_text(encoding="utf-8"))
            # A caller that edits what it was handed cannot change the next one.
            variables["MISE_OFFLINE"] = "0"
            self.assertEqual(
                "1", provider.measurement_environment(MANIFEST)["MISE_OFFLINE"]
            )

    def test_the_search_for_configuration_stops_above_the_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            provider = mise.Mise(Path(temp))

            variables = provider.measurement_environment(
                Path("/home/operator/project/sub/.mise.toml")
            )

            # The manifest's own directory is read and trusted; its parent is
            # the ceiling, so nothing above the manifest is read.
            self.assertEqual(".mise.toml", variables["MISE_OVERRIDE_CONFIG_FILENAMES"])
            self.assertEqual(
                "/home/operator/project/sub", variables["MISE_TRUSTED_CONFIG_PATHS"]
            )
            self.assertEqual("/home/operator/project", variables["MISE_CEILING_PATHS"])


class LockDisagreementTests(unittest.TestCase):
    """Where the lockfile no longer pins what the manifest declares."""

    def test_a_lockfile_pinning_every_declared_specifier_agrees(self) -> None:
        self.assertEqual([], mise.lock_disagreements(mise_manifest(), mise_lock()))

    def test_a_tool_the_lockfile_has_no_entry_for(self) -> None:
        manifest = '[tools]\njava = "21"\nmaven = "3.9"\n'
        lock = '[[tools.java]]\nversion = "21.0.2"\nspecifiers = ["21"]\n'

        self.assertEqual(
            ["maven is declared and not in the lockfile"],
            mise.lock_disagreements(manifest, lock),
        )

    def test_a_specifier_the_lockfile_pinned_no_version_for(self) -> None:
        manifest = '[tools]\njava = "22"\n'
        lock = '[[tools.java]]\nversion = "21.0.2"\nspecifiers = ["21"]\n'

        self.assertEqual(
            ["java@22 is not in the lockfile"], mise.lock_disagreements(manifest, lock)
        )

    def test_reads_every_form_a_specifier_takes(self) -> None:
        manifest = '[tools]\njava = ["21", "17"]\nmaven = { version = "3.9" }\n'
        lock = (
            '[[tools.java]]\nversion = "21.0.2"\nspecifiers = ["21"]\n'
            '[[tools.maven]]\nversion = "3.9.16"\nspecifiers = ["3.9"]\n'
        )

        self.assertEqual(
            ["java@17 is not in the lockfile"], mise.lock_disagreements(manifest, lock)
        )

    def test_a_manifest_declaring_no_tool_agrees_with_any_lockfile(self) -> None:
        self.assertEqual([], mise.lock_disagreements("[tasks]\n", ""))
        self.assertEqual([], mise.lock_disagreements("[tasks]\n", mise_lock()))

    def test_an_unreadable_file_is_a_disagreement(self) -> None:
        [statement] = mise.lock_disagreements(mise_manifest(), "[[tools\n")

        self.assertTrue(
            statement.startswith("the manifest or the lockfile is not readable as TOML")
        )


class FakeMiseTests(unittest.TestCase):
    """Freezing and installing through the stand-in the cases run on."""

    def _provider(
        self, root: Path, *, stale_lock: bool = False
    ) -> tuple[mise.Mise, Path]:
        repo = root / "repo"
        bin_dir = root / "bin"
        repo.mkdir()
        bin_dir.mkdir()
        write_mise_provider(bin_dir, repo, stale_lock=stale_lock)
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
                    "CODESERVO_TEST_MISE_LOG": str(root / "mise.log"),
                },
            )
        )
        return mise.Mise(root / "state" / "providers" / "mise"), repo / "mise.toml"

    def _calls(self, root: Path) -> list[dict]:
        log = root / "mise.log"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    def _freeze(self, provider: mise.Mise, manifest: Path, *tasks: str):
        return provider.freeze(
            manifest=manifest,
            lock_path="mise.lock",
            environment="default",
            tasks=tasks or (MISE_TASK,),
        )

    def test_freezes_the_pinned_toolchain_and_the_declared_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            provider, manifest = self._provider(root)

            environment = self._freeze(provider, manifest)

            self.assertEqual("2026.9.1-test test-os-test-arch", environment.version)
            self.assertEqual("test-os-test-arch", environment.platform)
            self.assertEqual((MISE_TASK, MISE_SENSOR_TASK), environment.tasks)
            self.assertEqual(
                [
                    {
                        "name": MISE_TOOL,
                        "version": MISE_TOOL_VERSION,
                        "requested": MISE_TOOL_VERSION[:1],
                        "installed": False,
                    }
                ],
                environment.packages,
            )
            self.assertEqual(str(provider.data_dir / "installs"), environment.prefix)
            calls = self._calls(root)
            self.assertEqual(["ls", "version", "tasks"], [c["args"][0] for c in calls])
            # Every question is a measurement: offline, locked, one manifest.
            for call in calls:
                self.assertEqual("1", call["env"]["MISE_OFFLINE"])
                self.assertEqual("1", call["env"]["MISE_LOCKED"])
                self.assertEqual(str(provider.data_dir), call["env"]["MISE_DATA_DIR"])
                self.assertEqual(
                    "mise.toml", call["env"]["MISE_OVERRIDE_CONFIG_FILENAMES"]
                )

    def test_refuses_a_manifest_that_moved_past_its_lockfile_before_asking(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            provider, manifest = self._provider(root, stale_lock=True)

            with self.assertRaisesRegex(
                ProviderError,
                "mise.lock is not consistent with the manifest it locks:"
                f" {MISE_TOOL}@{MISE_TOOL_VERSION[:1]} is not in the lockfile",
            ):
                self._freeze(provider, manifest)

            # The verdict is read from the two files: mise was never asked.
            self.assertEqual([], self._calls(root))

    def test_refuses_a_task_the_manifest_does_not_declare(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            provider, manifest = self._provider(root)

            with self.assertRaisesRegex(
                ProviderError, "mise.toml declares no task absent-task"
            ):
                self._freeze(provider, manifest, MISE_TASK, "absent-task")

    def test_reads_the_warning_the_inventory_hides_a_missing_pin_under(self) -> None:
        provider = mise.Mise(DATA_DIR)
        answered = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="{}",
            stderr=(
                "mise WARN  Failed to resolve tool version list for java:"
                " java@21 is not in the lockfile\n"
            ),
        )

        with patch.object(mise.Mise, "_capture", return_value=answered):
            with self.assertRaisesRegex(
                ProviderError,
                "mise.lock is not consistent with the manifest it locks:"
                " mise WARN .* java@21 is not in the lockfile",
            ):
                provider._inventory(MANIFEST, "mise.lock")

    def test_refuses_a_description_that_is_not_json(self) -> None:
        provider = mise.Mise(DATA_DIR)
        answered = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="mise 2026.9.1\n", stderr=""
        )

        with patch.object(mise.Mise, "_capture", return_value=answered):
            with self.assertRaisesRegex(
                ProviderError, "mise version answered no readable JSON"
            ):
                provider._description(MANIFEST)

    def test_refuses_to_measure_without_the_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            (repo / "mise.toml").write_text(mise_manifest(), encoding="utf-8")
            (repo / "mise.lock").write_text(mise_lock(), encoding="utf-8")
            provider = mise.Mise(root / "data")

            with patch.dict(os.environ, {"PATH": str(root / "empty")}):
                with self.assertRaisesRegex(ProviderError, "cannot run mise ls"):
                    self._freeze(provider, repo / "mise.toml")

    def test_installs_into_the_controllers_directory_locked_and_online(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            provider, manifest = self._provider(root)

            installation = provider.install(manifest=manifest, environment="default")

            self.assertEqual(0, installation.exit_code)
            self.assertEqual(
                ("mise", "install", "-C", str(manifest.parent)), installation.command
            )
            self.assertEqual(
                str(provider.data_dir / "installs"), installation.prefix_path
            )
            self.assertTrue(
                (
                    provider.data_dir / "installs" / MISE_TOOL / MISE_TOOL_VERSION
                ).is_dir()
            )
            self.assertGreaterEqual(installation.duration_ms, 0)
            self.assertEqual("mise installed", installation.diagnostic)
            [call] = self._calls(root)
            # Locked, so nothing unpinned is resolved; not offline, so what
            # the lockfile pins can be fetched.
            self.assertEqual("1", call["env"]["MISE_LOCKED"])
            self.assertNotIn("MISE_OFFLINE", call["env"])
            self.assertEqual(str(provider.data_dir), call["env"]["MISE_DATA_DIR"])
            # The tree holding the manifest is only read.
            self.assertEqual(
                {"mise.toml", "mise.lock"},
                {path.name for path in manifest.parent.iterdir()},
            )
            # Once installed, the inventory says so.
            self.assertTrue(self._freeze(provider, manifest).packages[0]["installed"])

    def test_reports_an_installation_the_provider_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            provider, manifest = self._provider(root)

            with patch.dict(os.environ, {"CODESERVO_TEST_MISE_INSTALL_FAILS": "1"}):
                installation = provider.install(
                    manifest=manifest, environment="default"
                )

            self.assertEqual(1, installation.exit_code)
            self.assertEqual("mise ERROR failed to install", installation.diagnostic)
            self.assertFalse(Path(installation.prefix_path).exists())


if __name__ == "__main__":
    unittest.main()
