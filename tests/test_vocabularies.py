"""What every closed vocabulary the package declares is held to.

A vocabulary is enumerated so that its members are the whole of it, and it is
a `StrEnum` so that a member is the string it serialises to. The record, the
journal, the published inventory and the verification report are all written
from these members and read back as plain strings, so the two must stay
interchangeable: nothing downstream may ask whether a value is an instance of
a vocabulary.
"""

import enum
import importlib
import json
import pkgutil
import unittest

import codeservo


def declared_vocabularies() -> list[type[enum.Enum]]:
    """Every enumeration the package declares, wherever it declares it."""
    found: dict[str, type[enum.Enum]] = {}
    for module in pkgutil.walk_packages(codeservo.__path__, f"{codeservo.__name__}."):
        # `__main__` runs the command line on import rather than declaring
        # anything, so the walk names it and does not import it.
        if module.name.rsplit(".", 1)[-1] == "__main__":
            continue
        imported = importlib.import_module(module.name)
        for value in vars(imported).values():
            if (
                isinstance(value, type)
                and issubclass(value, enum.Enum)
                and value.__module__.startswith(f"{codeservo.__name__}.")
            ):
                found[f"{value.__module__}.{value.__qualname__}"] = value
    return [found[name] for name in sorted(found)]


class VocabularyTests(unittest.TestCase):
    def test_the_package_declares_vocabularies(self) -> None:
        # The walk finding nothing would make every assertion below vacuous.
        self.assertNotEqual([], declared_vocabularies())

    def test_every_vocabulary_is_a_string_enumeration(self) -> None:
        for vocabulary in declared_vocabularies():
            with self.subTest(vocabulary=vocabulary.__qualname__):
                self.assertTrue(
                    issubclass(vocabulary, enum.StrEnum),
                    f"{vocabulary.__module__}.{vocabulary.__qualname__}"
                    " must be a StrEnum to reach a record as its own value",
                )

    def test_a_member_serialises_as_the_value_and_not_as_its_name(self) -> None:
        for vocabulary in declared_vocabularies():
            for member in vocabulary:
                with self.subTest(member=str(member)):
                    self.assertEqual(json.dumps(member.value), json.dumps(member))
                    self.assertEqual(member.value, f"{member}")

    def test_a_member_read_back_from_json_is_the_member_it_was_written_from(
        self,
    ) -> None:
        for vocabulary in declared_vocabularies():
            for member in vocabulary:
                with self.subTest(member=str(member)):
                    read_back = json.loads(json.dumps({"field": member}))["field"]

                    # A record holds plain strings. They compare equal to the
                    # member, index a mapping keyed by it, and name it back.
                    self.assertEqual(member, read_back)
                    self.assertEqual(member, vocabulary(read_back))
                    self.assertEqual(1, {member: 1}[read_back])
                    self.assertIn(read_back, vocabulary)


if __name__ == "__main__":
    unittest.main()
