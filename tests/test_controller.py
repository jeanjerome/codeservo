import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

import codeservo
from codeservo.actuator import Actuator
from codeservo.controller import (
    EVIDENCE_SCHEMA_VERSION,
    NO_ENVIRONMENT,
    ControlFailure,
    _altered_sensors,
    _candidate_digests,
    _changed_environment,
    _command_version,
    _frozen_environment,
    _inference,
    _mutated,
    _observations,
    _protected_paths,
    _record_actuation,
    _resolve_state_dir,
    _resolved_environment,
    _review_schema_path,
    _runtime_metadata,
)
from codeservo.evidence import sha256_file, sha256_path
from codeservo.model import (
    Constitution,
    ExecutionEnvironment,
    Gate,
    ReviewPolicy,
    ScopePolicy,
)
from harness import PIXI_PACKAGES, PIXI_TASK, commit_repository, write_provider

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


PROFILE_FIELDS = {"requested", "validation", "native", "observed", "provenance"}


class InferenceProfileTests(unittest.TestCase):
    """Both requested profiles are frozen before anything actuates."""

    def _request(self, **overrides) -> dict:
        request = {
            "backend": "claude",
            "model": "opus",
            "effort": "high",
            "speed": "standard",
        }
        request.update(overrides)
        return request

    def _inference(self, implementer=None, reviewer=None) -> dict:
        return _inference(
            implementer=self._request(**(implementer or {})),
            reviewer=self._request(**(reviewer or {})),
        )

    def _implementer(self, **overrides) -> dict:
        return self._inference(implementer=overrides)["implementer"]

    def test_holds_the_two_roles_with_the_same_five_fields(self) -> None:
        inference = self._inference()

        self.assertEqual({"implementer", "reviewer"}, set(inference))
        self.assertEqual(PROFILE_FIELDS, set(inference["implementer"]))
        self.assertEqual(PROFILE_FIELDS, set(inference["reviewer"]))

    def test_freezes_each_role_as_it_was_resolved(self) -> None:
        inference = self._inference(
            reviewer={"backend": "codex", "model": "a-model", "speed": "fast"}
        )

        self.assertEqual(
            {
                "backend": "claude",
                "model": "opus",
                "effort": "high",
                "speed": "standard",
            },
            inference["implementer"]["requested"],
        )
        self.assertEqual(
            {
                "backend": "codex",
                "model": "a-model",
                "effort": "high",
                "speed": "fast",
            },
            inference["reviewer"]["requested"],
        )

    def test_records_an_absent_review_effort_as_null(self) -> None:
        reviewer = self._inference(reviewer={"effort": None})["reviewer"]

        self.assertIsNone(reviewer["requested"]["effort"])

    def test_checks_each_role_against_the_inventory_of_its_own_backend(self) -> None:
        """One backend's inventory never answers for the other's."""
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp)
            (codex_home / "models_cache.json").write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "slug": "a-model",
                                "supported_reasoning_levels": [{"effort": "high"}],
                                "visibility": "list",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                # The same model and effort for both roles: only the backend
                # that lists them can settle the request.
                inference = self._inference(
                    implementer={"backend": "claude", "model": "a-model"},
                    reviewer={"backend": "codex", "model": "a-model"},
                )

        self.assertEqual("unverified", inference["implementer"]["validation"]["status"])
        self.assertEqual(
            "unavailable", inference["implementer"]["validation"]["inventory_source"]
        )
        self.assertEqual("supported", inference["reviewer"]["validation"]["status"])
        self.assertEqual(
            "backend-cache", inference["reviewer"]["validation"]["inventory_source"]
        )

    def test_holds_nothing_either_backend_has_answered_yet(self) -> None:
        for role, profile in self._inference().items():
            with self.subTest(role=role):
                self.assertIsNone(profile["native"])
                self.assertEqual(
                    {"model": None, "effort": None, "speed": None},
                    profile["observed"],
                )
                self.assertEqual("incomplete", profile["provenance"])

    def test_freezes_the_four_requested_fields(self) -> None:
        implementer = self._implementer(speed="fast")

        self.assertEqual(
            {
                "backend": "claude",
                "model": "opus",
                "effort": "high",
                "speed": "fast",
            },
            implementer["requested"],
        )

    def test_records_an_absent_effort_as_null(self) -> None:
        self.assertIsNone(self._implementer(effort=None)["requested"]["effort"])

    def test_holds_nothing_the_backend_has_not_answered_yet(self) -> None:
        implementer = self._implementer()

        self.assertIsNone(implementer["native"])
        self.assertEqual(
            {"model": None, "effort": None, "speed": None}, implementer["observed"]
        )
        self.assertEqual("incomplete", implementer["provenance"])
        # A backend with no verified cache cannot contradict the request.
        self.assertEqual("unverified", implementer["validation"]["status"])
        self.assertEqual(
            {"status", "reason", "inventory_source"}, set(implementer["validation"])
        )


class ActuationRecordTests(unittest.TestCase):
    def _profile(self) -> dict:
        return {
            "native": {"--effort": "max"},
            "observed": {"model": "claude-opus-5", "effort": None, "speed": None},
            "provenance": "complete",
        }

    def test_reports_a_known_model_as_complete(self) -> None:
        profile = {"native": None, "observed": {}, "provenance": "incomplete"}

        _record_actuation(
            profile,
            {
                "native": {"model_reasoning_effort": "high"},
                "observed": {
                    "model": "gpt-5.6-sol",
                    "effort": "high",
                    "speed": None,
                },
            },
        )

        self.assertEqual({"model_reasoning_effort": "high"}, profile["native"])
        self.assertEqual("gpt-5.6-sol", profile["observed"]["model"])
        self.assertEqual("complete", profile["provenance"])

    def test_keeps_no_value_from_an_earlier_actuation(self) -> None:
        profile = self._profile()

        _record_actuation(
            profile,
            {
                "native": {},
                "observed": {"model": None, "effort": None, "speed": None},
            },
        )

        self.assertEqual({}, profile["native"])
        self.assertEqual(
            {"model": None, "effort": None, "speed": None}, profile["observed"]
        )
        self.assertEqual("incomplete", profile["provenance"])


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


class ExecutionEnvironmentTests(unittest.TestCase):
    """The environment is frozen from the base commit, before anything runs."""

    def _repo(self) -> tuple[Path, Path, str]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        repo = root / "repo"
        bin_dir = root / "bin"
        repo.mkdir()
        bin_dir.mkdir()
        write_provider(bin_dir, repo)
        commit_repository(repo)
        base_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        path = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
        patcher = patch.dict(os.environ, {"PATH": path}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        return root, repo, base_commit

    def _execution(self) -> ExecutionEnvironment:
        return ExecutionEnvironment(
            provider="pixi",
            manifest="pyproject.toml",
            lock="pixi.lock",
            environment="default",
        )

    def test_a_run_declaring_no_provider_records_none(self) -> None:
        self.assertEqual({"provider": "none"}, NO_ENVIRONMENT)

    def test_digests_the_two_files_the_base_commit_holds(self) -> None:
        _, repo, base_commit = self._repo()
        frozen = _frozen_environment(repo, base_commit, self._execution())

        # A later edit of the working tree cannot change what was frozen.
        (repo / "pyproject.toml").write_text("[tool.pixi]\n", encoding="utf-8")

        self.assertEqual("pixi", frozen["provider"])
        self.assertEqual("pyproject.toml", frozen["manifest_path"])
        self.assertEqual("pixi.lock", frozen["lock_path"])
        self.assertEqual("default", frozen["environment"])
        self.assertEqual(
            _frozen_environment(repo, base_commit, self._execution()), frozen
        )
        self.assertNotEqual(
            sha256_file(repo / "pyproject.toml"), frozen["manifest_sha256"]
        )

    def test_refuses_a_manifest_the_base_commit_does_not_hold(self) -> None:
        _, repo, base_commit = self._repo()
        execution = ExecutionEnvironment(
            provider="pixi",
            manifest="untracked.toml",
            lock="pixi.lock",
            environment="default",
        )

        with self.assertRaisesRegex(ControlFailure, "untracked.toml is not committed"):
            _frozen_environment(repo, base_commit, execution)

    def test_stores_the_inventory_the_lockfile_resolves_to(self) -> None:
        root, repo, _ = self._repo()
        run_dir = root / "run"

        resolved, prefix = _resolved_environment(
            repo, run_dir, self._execution(), (PIXI_TASK,)
        )

        # The directory the provider reports is returned, never recorded.
        self.assertEqual(str(repo / ".pixi" / "envs" / "default"), prefix)
        self.assertNotIn("prefix", resolved)
        stored = run_dir / "environment" / "packages.json"
        self.assertEqual("environment/packages.json", resolved["packages_path"])
        self.assertEqual(PIXI_PACKAGES, json.loads(stored.read_text()))
        self.assertEqual(sha256_file(stored), resolved["packages_sha256"])
        self.assertEqual(len(PIXI_PACKAGES), resolved["package_count"])
        self.assertEqual("0.77.1-test", resolved["provider_version"])
        self.assertEqual("test-platform", resolved["platform"])
        self.assertEqual([PIXI_TASK], resolved["declared_tasks"])

    def test_records_nothing_the_description_says_about_the_operator(self) -> None:
        root, repo, _ = self._repo()

        resolved, _ = _resolved_environment(
            repo, root / "run", self._execution(), (PIXI_TASK,)
        )

        serialized = json.dumps(resolved)
        for private in ("cache_dir", "auth_dir", "config_locations", "global_info"):
            self.assertNotIn(private, serialized)
            self.assertNotIn(private, resolved)
        self.assertNotIn("/operator", serialized)


class CandidateEnvironmentTests(unittest.TestCase):
    """What the candidate was prepared with, rechecked after each phase."""

    def _candidate(self) -> tuple[Path, dict, ExecutionEnvironment]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        worktree = Path(temp.name)
        (worktree / "pyproject.toml").write_text("[tool.pixi]\n", encoding="utf-8")
        (worktree / "pixi.lock").write_text("version: 6\n", encoding="utf-8")
        execution = ExecutionEnvironment(
            provider="pixi",
            manifest="pyproject.toml",
            lock="pixi.lock",
            environment="default",
        )
        environment = {
            "provider": "pixi",
            "candidate": {
                "prefix_path": str(worktree / ".pixi" / "envs" / "default"),
                "command": ["pixi", "install"],
                "exit_code": 0,
                "duration_ms": 1,
                **_candidate_digests(worktree, execution),
                "unchanged_at_end": True,
            },
        }
        return worktree, environment, execution

    def test_a_workspace_nobody_touched_is_unchanged(self) -> None:
        worktree, environment, execution = self._candidate()
        candidate = environment["candidate"]

        self.assertEqual([], _changed_environment(environment, worktree, execution))
        self.assertIsNone(candidate["config_sha256"])
        self.assertTrue(candidate["unchanged_at_end"])

    def test_names_the_lockfile_a_run_rewrote(self) -> None:
        worktree, environment, execution = self._candidate()
        (worktree / "pixi.lock").write_text("version: 6\n# resolved\n", encoding="utf-8")

        reasons = _changed_environment(environment, worktree, execution)

        self.assertEqual(
            ["execution environment: pixi.lock changed during the run"], reasons
        )
        self.assertFalse(environment["candidate"]["unchanged_at_end"])

    def test_names_a_provider_configuration_that_appeared(self) -> None:
        worktree, environment, execution = self._candidate()
        configuration = worktree / ".pixi" / "config.toml"
        configuration.parent.mkdir(parents=True)
        configuration.write_text("detached-environments = true\n", encoding="utf-8")

        reasons = _changed_environment(environment, worktree, execution)

        self.assertEqual(
            ["execution environment: .pixi/config.toml changed during the run"],
            reasons,
        )
        self.assertFalse(environment["candidate"]["unchanged_at_end"])

    def test_a_manifest_that_is_gone_is_a_change_and_not_a_crash(self) -> None:
        worktree, environment, execution = self._candidate()
        (worktree / "pyproject.toml").unlink()

        reasons = _changed_environment(environment, worktree, execution)

        self.assertEqual(
            ["execution environment: pyproject.toml changed during the run"], reasons
        )

    def test_a_run_declaring_no_provider_has_nothing_to_recompute(self) -> None:
        worktree, _, _ = self._candidate()

        self.assertEqual(
            [], _changed_environment(dict(NO_ENVIRONMENT), worktree, None)
        )


class ProtectedPathTests(unittest.TestCase):
    """What stays readable and stops being writable in a measured tree."""

    def _execution(self, manifest: str = "pyproject.toml") -> ExecutionEnvironment:
        return ExecutionEnvironment(
            provider="pixi",
            manifest=manifest,
            lock="pixi.lock",
            environment="default",
        )

    def test_protects_the_git_metadata_and_the_provider_directory(self) -> None:
        self.assertEqual(
            (Path("/tree/.git"), Path("/tree/.pixi")),
            _protected_paths(Path("/tree"), self._execution()),
        )

    def test_a_nested_manifest_names_its_own_workspace_directory(self) -> None:
        execution = self._execution("sub/pyproject.toml")

        self.assertEqual(
            (Path("/tree/.git"), Path("/tree/sub/.pixi")),
            _protected_paths(Path("/tree"), execution),
        )

    def test_a_run_declaring_no_provider_protects_the_metadata_only(self) -> None:
        self.assertEqual((Path("/tree/.git"),), _protected_paths(Path("/tree"), None))

    def test_each_tree_names_its_own_paths_and_never_the_other(self) -> None:
        source = _protected_paths(Path("/source"), self._execution())
        candidate = _protected_paths(Path("/candidate"), self._execution())

        self.assertTrue(all(path.is_relative_to("/source") for path in source))
        self.assertTrue(all(path.is_relative_to("/candidate") for path in candidate))


class MeasuredMutationTests(unittest.TestCase):
    """A phase that moved the tree it measured is a control failure."""

    def test_a_phase_that_left_the_tree_alone_reports_nothing(self) -> None:
        state = {"path": "observed.patch", "sha256": "a" * 64}

        self.assertEqual([], _mutated("quick", state, dict(state)))

    def test_names_the_phase_that_changed_the_candidate(self) -> None:
        before = {"path": "observed.patch", "sha256": "a" * 64}
        after = {"path": "full.patch", "sha256": "b" * 64}

        self.assertEqual(
            ["quick gates changed the candidate workspace"],
            _mutated("quick", before, after),
        )
        self.assertEqual(
            ["full gates changed the candidate workspace"],
            _mutated("full", before, after),
        )


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


class RuntimeIdentityTests(unittest.TestCase):
    def _actuator(self, name: str = "fake", version: str = "fake 9.9") -> Actuator:
        return Actuator(
            name=name,
            version_command=(sys.executable, "-c", f"print({version!r})"),
            implement=lambda *args, **kwargs: {},
            review=lambda *args, **kwargs: ({}, {}),
            describe_isolation=lambda *args, **kwargs: {},
        )

    def _source_root(self) -> Path:
        return Path(codeservo.__file__).resolve().parents[2]

    def test_declares_the_shape_the_record_has(self) -> None:
        # One isolation document per measured tree, and the state of the
        # candidate after the full gates, next to what the previous shape held.
        self.assertEqual(13, EVIDENCE_SCHEMA_VERSION)

    def test_names_both_backends_when_one_serves_both_roles(self) -> None:
        actuator = self._actuator()

        runtime = _runtime_metadata(actuator, actuator, None, None)

        self.assertEqual("fake", runtime["actuator"])
        self.assertEqual("fake", runtime["review_actuator"])
        self.assertEqual("fake 9.9", runtime["actuator_version"])
        self.assertEqual("fake 9.9", runtime["review_actuator_version"])
        self.assertEqual("fake-default", runtime["implementer_model"])
        self.assertEqual("fake-default", runtime["reviewer_model"])

    def test_names_each_backend_and_its_own_cli_version(self) -> None:
        runtime = _runtime_metadata(
            self._actuator(),
            self._actuator(name="other", version="other 1.2"),
            "a-model",
            "another-model",
        )

        self.assertEqual("fake", runtime["actuator"])
        self.assertEqual("fake 9.9", runtime["actuator_version"])
        self.assertEqual("other", runtime["review_actuator"])
        self.assertEqual("other 1.2", runtime["review_actuator_version"])
        self.assertEqual("a-model", runtime["implementer_model"])
        self.assertEqual("another-model", runtime["reviewer_model"])

    def test_reports_the_reviewing_backend_default_model(self) -> None:
        runtime = _runtime_metadata(
            self._actuator(), self._actuator(name="other"), None, None
        )

        self.assertEqual("fake-default", runtime["implementer_model"])
        self.assertEqual("other-default", runtime["reviewer_model"])

    def test_declares_the_controller_version_in_one_place(self) -> None:
        pyproject = self._source_root() / "pyproject.toml"
        if not pyproject.is_file():
            self.skipTest("controller does not run from a source checkout")
        declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))

        self.assertIn("version", declared["project"]["dynamic"])
        self.assertNotIn("version", declared["project"])
        self.assertEqual(
            "src/codeservo/__init__.py",
            declared["tool"]["hatch"]["version"]["path"],
        )

    def test_reports_the_single_declared_controller_version(self) -> None:
        runtime = _runtime_metadata(
            self._actuator(), self._actuator(), None, None
        )

        self.assertEqual(codeservo.__version__, runtime["codeservo_version"])

    def test_reports_the_commit_of_the_controller_checkout(self) -> None:
        checkout = subprocess.run(
            ["git", "-C", str(self._source_root()), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=False,
        )
        if checkout.returncode != 0:
            self.skipTest("controller does not run from a Git checkout")

        runtime = _runtime_metadata(
            self._actuator(), self._actuator(), None, None
        )

        self.assertEqual(checkout.stdout.strip(), runtime["codeservo_commit"])
        self.assertEqual(40, len(runtime["codeservo_commit"]))

    def test_keeps_the_answer_of_a_successful_lookup(self) -> None:
        self.assertEqual(
            "fake 9.9",
            _command_version([sys.executable, "-c", "print('fake 9.9')"]),
        )

    def test_reports_a_failed_lookup_as_unavailable(self) -> None:
        failing = [
            sys.executable,
            "-c",
            "import sys; print('fatal: not a git repository', file=sys.stderr);"
            " sys.exit(128)",
        ]

        self.assertEqual("unavailable", _command_version(failing))


if __name__ == "__main__":
    unittest.main()
