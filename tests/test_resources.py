"""Locating the documents the package publishes."""

import json
import tempfile
import unittest
from pathlib import Path

from codeservo.evidence.digests import sha256_file
from codeservo.resources import model_catalogue, review_schema


class ReviewSchemaTests(unittest.TestCase):
    def test_prefers_the_repository_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository_copy = root / "templates" / "review.schema.json"
            repository_copy.parent.mkdir()
            repository_copy.write_text("{}", encoding="utf-8")

            self.assertEqual(repository_copy, review_schema(root))

    def test_falls_back_to_the_packaged_schema_without_repository_templates(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            packaged = review_schema(Path(temp))

            self.assertTrue(packaged.is_file(), f"missing packaged schema: {packaged}")
            schema = json.loads(packaged.read_text(encoding="utf-8"))
            self.assertEqual({"criteria", "findings"}, set(schema["required"]))
            self.assertEqual(
                {"criteria", "findings"}, set(schema["properties"])
            )

    def test_both_copies_state_the_same_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            packaged = review_schema(Path(temp))
        repository_copy = review_schema()

        self.assertNotEqual(packaged, repository_copy)
        self.assertEqual(sha256_file(packaged), sha256_file(repository_copy))


class ModelCatalogueTests(unittest.TestCase):
    """The catalogue is published twice, and the two copies say the same."""

    def test_falls_back_to_the_packaged_catalogue_without_repository_templates(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            packaged = model_catalogue(Path(temp))

            self.assertTrue(packaged.is_file(), f"missing packaged catalogue: {packaged}")

    def test_both_copies_state_the_same_catalogue(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            packaged = model_catalogue(Path(temp))
        repository_copy = model_catalogue()

        self.assertNotEqual(packaged, repository_copy)
        self.assertEqual(sha256_file(packaged), sha256_file(repository_copy))


if __name__ == "__main__":
    unittest.main()
