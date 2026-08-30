import json
import tempfile
import unittest
from pathlib import Path

from codeservo.controller import (
    ControlFailure,
    _altered_sensors,
    _resolve_state_dir,
    _review_schema_path,
)
from codeservo.evidence import sha256_file, sha256_path


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
