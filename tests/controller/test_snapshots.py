"""Comparing the candidate across the boundaries of a measurement phase."""

import unittest

from codeservo.controller.document import FileRecord
from codeservo.controller.snapshots import mutated
from codeservo.domain.constitution import Phase


class MeasuredMutationTests(unittest.TestCase):
    """A phase that moved the tree it measured is a control failure."""

    def test_a_phase_that_left_the_tree_alone_reports_nothing(self) -> None:
        state = FileRecord(path="observed.patch", sha256="a" * 64)
        elsewhere = FileRecord(path="full.patch", sha256="a" * 64)

        # The digest is what is compared, never where the snapshot was kept.
        self.assertEqual([], mutated(Phase.QUICK, state, elsewhere))

    def test_names_the_phase_that_changed_the_candidate(self) -> None:
        before = FileRecord(path="observed.patch", sha256="a" * 64)
        after = FileRecord(path="full.patch", sha256="b" * 64)

        self.assertEqual(
            ["quick gates changed the candidate workspace"],
            mutated(Phase.QUICK, before, after),
        )
        self.assertEqual(
            ["full gates changed the candidate workspace"],
            mutated(Phase.FULL, before, after),
        )


if __name__ == "__main__":
    unittest.main()
