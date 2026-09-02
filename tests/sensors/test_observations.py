"""The contract a gate's structured document is held to."""

import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from codeservo.evidence.digests import sha256_file
from codeservo.resources import PACKAGE_DIR, SOURCE_ROOT
from codeservo.sensors.observations import (
    ABSENT,
    CONTRADICTED,
    FINDING_FIELDS,
    INVALID,
    OBSERVATION_FIELDS,
    SCHEMA_VERSION,
    SEVERITIES,
    STATUSES,
    VALID,
    classify,
    is_json_integer,
    is_json_number,
    schema_path,
    validate,
)

# The one copy an installed wheel always carries, and the one the criteria name.
PACKAGED_SCHEMA = PACKAGE_DIR / "observation.schema.json"

FINDING = {
    "id": "mutation-42",
    "severity": "major",
    "path": "src/example.py",
    "line": 18,
    "message": "conditional boundary survived",
}


def document(**overrides) -> dict:
    """The recorded shape, with whatever a case wants to change about it."""
    shape = {
        "schema_version": 1,
        "sensor": "mutation",
        "status": "failed",
        "summary": "3 surviving mutants",
        "findings": [dict(FINDING)],
        "metrics": {"killed": 37, "survived": 3, "timeout": 0},
    }
    shape.update(overrides)
    return shape


def encoded(payload) -> bytes:
    return json.dumps(payload).encode("utf-8")


def finding(**overrides) -> dict:
    changed = dict(FINDING)
    changed.update(overrides)
    return changed


class ValidObservationTests(unittest.TestCase):
    def test_accepts_the_recorded_shape(self) -> None:
        parsed, error = validate(encoded(document()))

        self.assertIsNone(error)
        self.assertEqual(document(), parsed)

    def test_accepts_an_observation_that_found_and_measured_nothing(self) -> None:
        parsed, error = validate(
            encoded(document(status="passed", summary="", findings=[], metrics={}))
        )

        self.assertIsNone(error)
        self.assertEqual([], parsed["findings"])

    def test_accepts_a_finding_naming_neither_file_nor_line(self) -> None:
        parsed, error = validate(
            encoded(document(findings=[finding(path=None, line=None)]))
        )

        self.assertIsNone(error)
        self.assertIsNone(parsed["findings"][0]["path"])

    def test_accepts_fractional_metrics(self) -> None:
        _, error = validate(encoded(document(metrics={"ratio": 0.925})))

        self.assertIsNone(error)


class RefusedObservationTests(unittest.TestCase):
    def _refused(self, payload) -> str:
        parsed, error = validate(encoded(payload))
        self.assertIsNone(parsed)
        self.assertIsInstance(error, str)
        return error

    def test_refuses_a_field_too_many(self) -> None:
        error = self._refused(document(extra="not part of the contract"))

        self.assertIn("unknown field extra", error)
        self.assertIn("carries exactly", error)

    def test_refuses_a_missing_field(self) -> None:
        incomplete = document()
        del incomplete["metrics"]

        error = self._refused(incomplete)

        self.assertIn("missing field metrics", error)

    def test_refuses_a_finding_with_a_field_of_its_own(self) -> None:
        error = self._refused(
            document(findings=[finding(confidence="high")])
        )

        self.assertIn("unknown field findings[0].confidence", error)
        self.assertIn("a finding carries exactly", error)

    def test_refuses_a_finding_missing_a_field(self) -> None:
        incomplete = finding()
        del incomplete["line"]

        error = self._refused(document(findings=[incomplete]))

        self.assertIn("missing field findings[0].line", error)

    def test_refuses_a_severity_outside_the_enumeration(self) -> None:
        error = self._refused(document(findings=[finding(severity="critical")]))

        self.assertIn("findings[0].severity", error)
        self.assertIn("blocker, info, major, minor", error)

    def test_refuses_a_status_outside_the_enumeration(self) -> None:
        error = self._refused(document(status="errored"))

        self.assertIn("field status", error)
        self.assertIn("failed, passed", error)

    def test_refuses_another_schema_version(self) -> None:
        error = self._refused(document(schema_version=2))

        self.assertIn("field schema_version must be the integer 1", error)

    def test_refuses_an_empty_sensor(self) -> None:
        error = self._refused(document(sensor=""))

        self.assertIn("field sensor must be a non-empty string", error)

    def test_refuses_a_summary_that_is_not_a_string(self) -> None:
        error = self._refused(document(summary=None))

        self.assertIn("field summary must be a string", error)

    def test_refuses_findings_that_are_not_an_array_of_objects(self) -> None:
        self.assertIn(
            "field findings must be an array of objects",
            self._refused(document(findings={})),
        )
        self.assertIn(
            "field findings[0] must be an object",
            self._refused(document(findings=["mutation-42"])),
        )

    def test_refuses_an_empty_identifier_or_message(self) -> None:
        self.assertIn(
            "field findings[0].id must be a non-empty string",
            self._refused(document(findings=[finding(id="")])),
        )
        self.assertIn(
            "field findings[0].message must be a non-empty string",
            self._refused(document(findings=[finding(message="")])),
        )

    def test_refuses_a_line_below_one_or_not_an_integer(self) -> None:
        for line in (0, -3, 18.5, "18"):
            with self.subTest(line=line):
                self.assertIn(
                    "field findings[0].line must be an integer of at least 1",
                    self._refused(document(findings=[finding(line=line)])),
                )

    def test_refuses_a_path_that_is_not_a_string_or_null(self) -> None:
        error = self._refused(document(findings=[finding(path=7)]))

        self.assertIn("field findings[0].path must be a string or null", error)

    def test_refuses_metrics_that_are_not_an_object_of_numbers(self) -> None:
        self.assertIn(
            "field metrics must be an object",
            self._refused(document(metrics=[])),
        )
        self.assertIn(
            "field metrics.killed must be a number",
            self._refused(document(metrics={"killed": "37"})),
        )

    def test_refuses_a_document_that_is_not_an_object(self) -> None:
        self.assertIn(
            "the observation must be a JSON object", self._refused([document()])
        )

    def test_refuses_what_is_not_json_at_all(self) -> None:
        parsed, error = validate(b"3 surviving mutants\n")

        self.assertIsNone(parsed)
        self.assertIn("the observation is not JSON", error)

    def test_refuses_bytes_that_are_not_utf_8(self) -> None:
        parsed, error = validate(b'{"sensor": "\xff\xfe"}')

        self.assertIsNone(parsed)
        self.assertEqual("the observation is not valid UTF-8", error)


class JsonTypeTests(unittest.TestCase):
    """Types are JSON's throughout, in every field and not only in `metrics`."""

    def test_refuses_the_constants_json_does_not_define(self) -> None:
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                parsed, error = validate(
                    b'{"metrics": {"ratio": ' + constant.encode() + b"}}"
                )

                self.assertIsNone(parsed)
                self.assertEqual(
                    f"the observation is not JSON: {constant} is not a JSON value",
                    error,
                )

    def test_a_boolean_is_never_a_number_and_never_an_integer(self) -> None:
        # Python's `bool` is an `int` and `True == 1`, so both predicates say
        # so once, and every field wanting a number is refused for that reason.
        self.assertFalse(is_json_number(True))
        self.assertFalse(is_json_integer(True))
        self.assertFalse(is_json_number(False))
        self.assertTrue(is_json_number(1))
        self.assertTrue(is_json_number(1.5))
        self.assertTrue(is_json_integer(1))
        self.assertFalse(is_json_integer(1.5))

    def test_refuses_a_boolean_metric_and_a_boolean_schema_version(self) -> None:
        _, metric = validate(encoded(document(metrics={"ok": True})))
        _, version = validate(encoded(document(schema_version=True)))

        self.assertEqual("field metrics.ok must be a number", metric)
        self.assertEqual("field schema_version must be the integer 1", version)

    def test_refuses_a_boolean_line(self) -> None:
        _, error = validate(encoded(document(findings=[finding(line=True)])))

        self.assertIn("field findings[0].line must be an integer", error)


class ClassificationTests(unittest.TestCase):
    """The exit code is the verdict; the document must agree with it."""

    def test_a_document_agreeing_with_the_exit_code_is_valid(self) -> None:
        self.assertEqual(
            (VALID, None),
            classify(encoded(document(status="passed")), passed=True),
        )
        self.assertEqual(
            (VALID, None),
            classify(encoded(document(status="failed")), passed=False),
        )

    def test_a_document_the_gate_never_wrote_is_absent(self) -> None:
        status, error = classify(None, passed=False)

        self.assertEqual(ABSENT, status)
        self.assertEqual("the gate wrote no observation", error)

    def test_a_document_breaking_the_contract_is_invalid(self) -> None:
        status, error = classify(encoded(document(status="errored")), passed=False)

        self.assertEqual(INVALID, status)
        self.assertIn("field status", error)

    def test_a_document_disagreeing_with_the_exit_code_is_contradicted(self) -> None:
        passing = classify(encoded(document(status="failed")), passed=True)
        failing = classify(encoded(document(status="passed")), passed=False)

        self.assertEqual(CONTRADICTED, passing[0])
        self.assertEqual(
            "the observation reports failed for a gate that passed", passing[1]
        )
        self.assertEqual(CONTRADICTED, failing[0])
        self.assertEqual(
            "the observation reports passed for a gate that did not pass",
            failing[1],
        )

    def test_the_fault_is_named_exactly_when_the_document_is_not_valid(self) -> None:
        cases = [
            classify(encoded(document(status="passed")), passed=True),
            classify(None, passed=False),
            classify(b"not json", passed=False),
            classify(encoded(document(status="passed")), passed=False),
        ]

        for status, error in cases:
            with self.subTest(status=status):
                self.assertEqual(status == VALID, error is None)


class PublishedSchemaTests(unittest.TestCase):
    """The published document and the enforced contract cannot drift apart."""

    def _schema(self) -> dict:
        return json.loads(PACKAGED_SCHEMA.read_text(encoding="utf-8"))

    def test_the_schema_declares_the_field_set_the_validation_enforces(self) -> None:
        schema = self._schema()

        self.assertEqual(OBSERVATION_FIELDS, set(schema["properties"]))
        self.assertEqual(OBSERVATION_FIELDS, set(schema["required"]))
        self.assertFalse(schema["additionalProperties"])

    def test_the_schema_declares_the_finding_the_validation_enforces(self) -> None:
        item = self._schema()["properties"]["findings"]["items"]

        self.assertEqual(FINDING_FIELDS, set(item["properties"]))
        self.assertEqual(FINDING_FIELDS, set(item["required"]))
        self.assertFalse(item["additionalProperties"])

    def test_the_schema_declares_the_two_enumerations_and_the_version(self) -> None:
        schema = self._schema()
        item = schema["properties"]["findings"]["items"]

        self.assertEqual(set(STATUSES), set(schema["properties"]["status"]["enum"]))
        self.assertEqual(
            set(SEVERITIES), set(item["properties"]["severity"]["enum"])
        )
        self.assertEqual(SCHEMA_VERSION, schema["properties"]["schema_version"]["const"])

    def test_both_copies_state_the_same_contract(self) -> None:
        repository_copy = schema_path()

        self.assertNotEqual(PACKAGED_SCHEMA, repository_copy)
        self.assertEqual(
            sha256_file(PACKAGED_SCHEMA), sha256_file(repository_copy)
        )

    def test_prefers_the_repository_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository_copy = root / "templates" / "observation.schema.json"
            repository_copy.parent.mkdir()
            repository_copy.write_text("{}", encoding="utf-8")

            self.assertEqual(repository_copy, schema_path(root))

    def test_falls_back_to_the_packaged_schema_without_repository_templates(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            packaged = schema_path(Path(temp))

        self.assertEqual(PACKAGED_SCHEMA, packaged)
        self.assertTrue(packaged.is_file())

    def test_the_wheel_carries_the_published_schema(self) -> None:
        pyproject = SOURCE_ROOT / "pyproject.toml"
        if not pyproject.is_file():
            self.skipTest("controller does not run from a source checkout")
        declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))

        self.assertIn(
            "src/codeservo/resources/observation.schema.json",
            declared["tool"]["hatch"]["build"]["targets"]["wheel"]["include"],
        )


if __name__ == "__main__":
    unittest.main()
