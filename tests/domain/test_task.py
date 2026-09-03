import tempfile
import unittest
from pathlib import Path

from codeservo.domain.task import (
    Criterion,
    Task,
    TaskError,
    Verification,
    criteria_by_gate,
    load_task,
    reviewed_criteria,
)


def _load(text: str) -> Task:
    """One task file, read the way a run reads it."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "TASK.md"
        path.write_text(text, encoding="utf-8")
        return load_task(path)


class TaskTests(unittest.TestCase):
    def test_extracts_acceptance_criteria(self):
        task = _load("- [AC1] one\n- [AC2] two\n")

        self.assertEqual(
            {
                "AC1": Criterion(id="AC1", text="one"),
                "AC2": Criterion(id="AC2", text="two"),
            },
            task.criteria,
        )

    def test_requires_criterion(self):
        with self.assertRaises(TaskError):
            _load("# Task\n")

    def test_refuses_the_same_criterion_twice(self):
        with self.assertRaises(TaskError):
            _load("- [AC1] one\n- [AC1] one again\n")

    def test_a_missing_file_is_refused_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TaskError):
                load_task(Path(tmp) / "absent.md")


class VerificationTests(unittest.TestCase):
    """What decides a criterion, read off the criterion itself."""

    def test_a_criterion_naming_nothing_is_decided_by_the_review(self):
        criterion = _load("- [AC1] one\n").criteria["AC1"]

        self.assertIsNone(criterion.gate)
        self.assertEqual(Verification.REVIEW, criterion.verification)

    def test_a_criterion_names_the_review(self):
        criterion = _load("- [AC1] one {review}\n").criteria["AC1"]

        self.assertEqual(Criterion(id="AC1", text="one"), criterion)
        self.assertEqual(Verification.REVIEW, criterion.verification)

    def test_a_criterion_names_the_gate_that_decides_it(self):
        criterion = _load("- [AC1] one {gate: unit}\n").criteria["AC1"]

        self.assertEqual(Criterion(id="AC1", text="one", gate="unit"), criterion)
        self.assertEqual(Verification.GATE, criterion.verification)

    def test_the_verification_is_read_whatever_it_is_spaced_with(self):
        for line in (
            "- [AC1] one {gate:unit}",
            "- [AC1] one   {  gate :  unit  }",
            "- [AC1] one{gate: unit}",
        ):
            with self.subTest(line=line):
                self.assertEqual(
                    Criterion(id="AC1", text="one", gate="unit"),
                    _load(f"{line}\n").criteria["AC1"],
                )

    def test_braces_the_criterion_itself_carries_are_not_a_verification(self):
        """A criterion may end on the document it pins, and often does."""
        criterion = _load('- [AC1] the body is {"status":"ok"}\n').criteria["AC1"]

        self.assertEqual('the body is {"status":"ok"}', criterion.text)
        self.assertIsNone(criterion.gate)

    def test_a_word_that_is_neither_is_left_to_the_criterion(self):
        criterion = _load("- [AC1] one {reviewers agree}\n").criteria["AC1"]

        self.assertEqual("one {reviewers agree}", criterion.text)
        self.assertIsNone(criterion.gate)

    def test_a_verification_this_reader_cannot_parse_is_refused(self):
        """A near miss is a control input nobody would notice going wrong.

        Kept as criterion text, each of these would be silently left to the
        reviewer while reading as though a gate decided it.
        """
        for body in ("{Review}", "{gate unit}", "{GATE: unit}", "{review: unit}"):
            with self.subTest(body=body):
                with self.assertRaises(TaskError) as refused:
                    _load(f"- [AC1] one {body}\n")
                self.assertIn("AC1", str(refused.exception))

    def test_a_gate_verification_naming_no_gate_is_refused(self):
        for body in ("{gate}", "{gate:}", "{gate:   }"):
            with self.subTest(body=body):
                with self.assertRaises(TaskError):
                    _load(f"- [AC1] one {body}\n")

    def test_a_criterion_that_states_nothing_is_refused(self):
        with self.assertRaises(TaskError):
            _load("- [AC1] {review}\n")

    def test_a_criterion_naming_two_verifications_is_refused(self):
        """Only the last would be read, and the other would look declared."""
        for line in (
            "- [AC1] one {gate: unit} {review}",
            "- [AC1] one {review} {gate: unit}",
            "- [AC1] one {gate: unit} {gate: lint}",
        ):
            with self.subTest(line=line):
                with self.assertRaises(TaskError) as refused:
                    _load(f"{line}\n")
                self.assertIn("two verifications", str(refused.exception))


class CriteriaSetTests(unittest.TestCase):
    """The two readings the controller takes of one set of criteria."""

    CRITERIA = {
        "AC1": Criterion(id="AC1", text="one"),
        "AC2": Criterion(id="AC2", text="two", gate="unit"),
        "AC3": Criterion(id="AC3", text="three", gate="unit"),
        "AC4": Criterion(id="AC4", text="four", gate="acceptance"),
    }

    def test_the_review_is_asked_only_about_the_criteria_left_to_it(self):
        self.assertEqual(["AC1"], list(reviewed_criteria(self.CRITERIA)))

    def test_every_criterion_is_reviewed_when_none_names_a_gate(self):
        criteria = {"AC1": Criterion(id="AC1", text="one")}

        self.assertEqual(criteria, reviewed_criteria(criteria))

    def test_a_gate_carries_the_criteria_that_named_it_in_the_tasks_order(self):
        self.assertEqual(
            {"unit": ("AC2", "AC3"), "acceptance": ("AC4",)},
            criteria_by_gate(self.CRITERIA),
        )

    def test_no_gate_is_named_when_every_criterion_is_reviewed(self):
        self.assertEqual({}, criteria_by_gate({"AC1": Criterion(id="AC1", text="a")}))


if __name__ == "__main__":
    unittest.main()
