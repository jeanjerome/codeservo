import tempfile
import unittest
from pathlib import Path

from codeservo.task import TaskError, load_task


class TaskTests(unittest.TestCase):
    def test_extracts_acceptance_criteria(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "TASK.md"
            p.write_text("- [AC1] one\n- [AC2] two\n", encoding="utf-8")
            task = load_task(p)
            self.assertEqual(task.criteria, {"AC1": "one", "AC2": "two"})

    def test_requires_criterion(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "TASK.md"
            p.write_text("# Task\n", encoding="utf-8")
            with self.assertRaises(TaskError):
                load_task(p)


if __name__ == "__main__":
    unittest.main()
