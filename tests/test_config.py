import tempfile
import unittest
from pathlib import Path

from codeservo.config import ConstitutionError, load_constitution


class ConstitutionTests(unittest.TestCase):
    def _write(self, body: str) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)
        cfg = repo / ".codeservo" / "constitution.toml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(body, encoding="utf-8")
        return repo

    def test_requires_quick_and_full_gates(self) -> None:
        repo = self._write(
            '''
[[gate]]
name = "only"
phase = "quick"
command = "true"
'''
        )
        with self.assertRaisesRegex(ConstitutionError, "full gate"):
            load_constitution(repo)

    def test_accepts_quick_and_full_gates(self) -> None:
        repo = self._write(
            '''
[[gate]]
name = "quick"
phase = "quick"
command = "true"

[[gate]]
name = "full"
phase = "full"
command = "true"
'''
        )
        constitution = load_constitution(repo)
        self.assertEqual(1, len(constitution.gates_for("quick")))
        self.assertEqual(1, len(constitution.gates_for("full")))


if __name__ == "__main__":
    unittest.main()
