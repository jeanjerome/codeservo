"""Comparing the candidate across the boundaries of a measurement phase."""

import unittest

from codeservo.controller.snapshots import mutated


class MeasuredMutationTests(unittest.TestCase):
    """A phase that moved the tree it measured is a control failure."""

    def test_a_phase_that_left_the_tree_alone_reports_nothing(self) -> None:
        state = {"path": "observed.patch", "sha256": "a" * 64}

        self.assertEqual([], mutated("quick", state, dict(state)))

    def test_names_the_phase_that_changed_the_candidate(self) -> None:
        before = {"path": "observed.patch", "sha256": "a" * 64}
        after = {"path": "full.patch", "sha256": "b" * 64}

        self.assertEqual(
            ["quick gates changed the candidate workspace"],
            mutated("quick", before, after),
        )
        self.assertEqual(
            ["full gates changed the candidate workspace"],
            mutated("full", before, after),
        )


if __name__ == "__main__":
    unittest.main()
