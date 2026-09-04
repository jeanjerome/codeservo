import tempfile
import unittest
from pathlib import Path

from codeservo.runtime.sandbox import Isolation, isolation_evidence


class IsolationTests(unittest.TestCase):
    def test_a_profile_naming_nothing_is_empty(self) -> None:
        self.assertTrue(Isolation().empty)
        self.assertFalse(Isolation(denied=(Path("/s"),)).empty)
        self.assertFalse(Isolation(read_only=(Path("/r"),)).empty)


class IsolationEvidenceTests(unittest.TestCase):
    def test_reports_absolute_paths_for_both_denial_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            evidence = isolation_evidence(
                Isolation(denied=(root / "sensors",), read_only=(root / "repo",)),
                "macos-sandbox-exec",
            )

            self.assertEqual("macos-sandbox-exec", evidence.mechanism)
            self.assertEqual((str(root / "sensors"),), evidence.denied_paths)
            self.assertEqual((str(root / "repo"),), evidence.read_only_paths)
            self.assertTrue(evidence.user_config_ignored)
            # The record carries JSON arrays, whatever the document holds.
            self.assertEqual(
                [str(root / "sensors")], evidence.to_document()["denied_paths"]
            )


if __name__ == "__main__":
    unittest.main()
