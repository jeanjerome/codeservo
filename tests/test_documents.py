"""What every document the package declares is held to.

A document is a frozen dataclass rendered as the JSON object a record, a
journal line or a digest is taken over. Two properties carry the record's
promises: nothing edits a document after it is built, and a field that was
never measured is absent from what is written rather than present as null.
"""

import enum
import importlib
import json
import pkgutil
import unittest
from dataclasses import dataclass, fields
from typing import Any

import codeservo
from codeservo.domain.document import UNSET, Document, Unset, rendered


def declared_documents() -> list[type[Document]]:
    """Every document the package declares, wherever it declares it."""
    found: dict[str, type[Document]] = {}
    for module in pkgutil.walk_packages(codeservo.__path__, f"{codeservo.__name__}."):
        # `__main__` runs the command line on import rather than declaring
        # anything, so the walk names it and does not import it.
        if module.name.rsplit(".", 1)[-1] == "__main__":
            continue
        imported = importlib.import_module(module.name)
        for value in vars(imported).values():
            if (
                isinstance(value, type)
                and issubclass(value, Document)
                and value is not Document
                and value.__module__.startswith(f"{codeservo.__name__}.")
            ):
                found[f"{value.__module__}.{value.__qualname__}"] = value
    return [found[name] for name in sorted(found)]


class Vocabulary(enum.StrEnum):
    MEMBER = "member"


@dataclass(frozen=True, kw_only=True)
class Leaf(Document):
    measured: str
    answered: str | None
    never_measured: str | None | Unset = UNSET


@dataclass(frozen=True, kw_only=True)
class Branch(Document):
    leaf: Leaf
    many: tuple[Leaf, ...]
    bag: dict[str, Any]
    named: Vocabulary


class DeclaredDocumentTests(unittest.TestCase):
    def test_the_package_declares_documents(self) -> None:
        # The walk finding nothing would make every assertion below vacuous.
        self.assertNotEqual([], declared_documents())

    def test_every_document_refuses_to_be_edited(self) -> None:
        for document in declared_documents():
            with self.subTest(document=document.__qualname__):
                self.assertTrue(
                    document.__dataclass_params__.frozen,
                    f"{document.__module__}.{document.__qualname__}"
                    " must be frozen: a record is written from what was"
                    " measured, never from what was edited afterwards",
                )

    def test_every_document_is_built_by_naming_its_fields(self) -> None:
        for document in declared_documents():
            for declared in fields(document):
                with self.subTest(document=document.__qualname__, field=declared.name):
                    self.assertTrue(
                        declared.kw_only or not declared.init,
                        f"{document.__qualname__}.{declared.name} must be"
                        " keyword-only: a document of many fields is not"
                        " built by position",
                    )


class RenderingTests(unittest.TestCase):
    def _leaf(self) -> Leaf:
        return Leaf(measured="here", answered=None)

    def test_a_field_nobody_measured_is_absent_and_a_null_answer_is_kept(
        self,
    ) -> None:
        document = self._leaf().to_document()

        # The two are different statements, and the record keeps them apart.
        self.assertNotIn("never_measured", document)
        self.assertIn("answered", document)
        self.assertIsNone(document["answered"])

    def test_a_measured_field_reaches_the_document_under_its_own_name(self) -> None:
        self.assertEqual("here", self._leaf().to_document()["measured"])

    def test_a_document_reached_through_a_field_a_list_or_a_mapping_renders(
        self,
    ) -> None:
        branch = Branch(
            leaf=self._leaf(),
            many=(self._leaf(),),
            bag={"nested": self._leaf()},
            named=Vocabulary.MEMBER,
        )

        document = branch.to_document()

        expected = {"measured": "here", "answered": None}
        self.assertEqual(expected, document["leaf"])
        self.assertEqual([expected], document["many"])
        self.assertEqual({"nested": expected}, document["bag"])
        self.assertEqual("member", document["named"])

    def test_what_a_document_renders_to_is_what_json_carries(self) -> None:
        branch = Branch(
            leaf=self._leaf(),
            many=(self._leaf(),),
            bag={"nested": self._leaf()},
            named=Vocabulary.MEMBER,
        )

        written = json.dumps(branch.to_document(), sort_keys=True)

        self.assertEqual(branch.to_document(), json.loads(written))

    def test_a_value_that_is_not_a_document_reaches_the_record_unchanged(
        self,
    ) -> None:
        for value in ("text", 1, 1.5, True, None):
            with self.subTest(value=value):
                self.assertIs(value, rendered(value))


if __name__ == "__main__":
    unittest.main()
