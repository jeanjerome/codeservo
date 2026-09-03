"""Rating what a session consumed, at the catalogue's list prices."""

import unittest

from codeservo.actuators.base import Billed, Tokens, Usage
from codeservo.actuators.catalogue import Backend, load_catalogue
from codeservo.controller.pricing import consumption

CATALOGUE = load_catalogue()


def _tokens(**counts) -> Tokens:
    declared = {"input": 0, "cached_input": 0, "cache_write": 0, "output": 0, "reasoning": 0}
    declared.update(counts)
    return Tokens(**declared)


class ConsumptionTests(unittest.TestCase):
    def test_rates_each_block_at_the_model_the_backend_named(self) -> None:
        usage = Usage(
            billed=(
                Billed(
                    model="claude-opus-5",
                    tokens=_tokens(input=1000000),
                    reported_cost_usd=5.0,
                ),
                Billed(
                    model="claude-haiku-4-5-20251001",
                    tokens=_tokens(output=1000000),
                    reported_cost_usd=5.0,
                ),
            ),
            cache_write_duration=None,
        )

        consumed = consumption(CATALOGUE, Backend.CLAUDE, "claude-opus-5", usage)

        self.assertEqual(
            [("claude-opus-5", "reported_model", 5.0), ("claude-haiku-4-5-20251001", "reported_model", 5.0)],
            [(item.model, item.basis, item.cost_usd) for item in consumed.items],
        )
        self.assertEqual(10.0, consumed.cost_usd)
        self.assertEqual([5.0, 5.0], [item.reported_cost_usd for item in consumed.items])

    def test_rates_an_unnamed_block_at_the_requested_model_and_says_so(self) -> None:
        usage = Usage(
            billed=(Billed(model=None, tokens=_tokens(input=1000000), reported_cost_usd=None),),
            cache_write_duration=None,
        )

        consumed = consumption(CATALOGUE, Backend.CODEX, "gpt-5.6-terra", usage)

        item = consumed.items[0]
        self.assertEqual("gpt-5.6-terra", item.model)
        self.assertEqual("requested_model", item.basis)
        self.assertEqual(2.0, item.cost_usd)
        self.assertIsNone(item.reported_cost_usd)
        self.assertEqual(2.0, consumed.cost_usd)

    def test_a_model_the_catalogue_does_not_list_keeps_its_tokens_unrated(self) -> None:
        usage = Usage(
            billed=(
                Billed(model="claude-opus-5", tokens=_tokens(input=1000000), reported_cost_usd=None),
                Billed(model="a-model-nobody-lists", tokens=_tokens(output=10), reported_cost_usd=0.1),
            ),
            cache_write_duration=None,
        )

        consumed = consumption(CATALOGUE, Backend.CLAUDE, "claude-opus-5", usage)

        self.assertEqual([5.0, None], [item.cost_usd for item in consumed.items])
        self.assertEqual(_tokens(output=10), consumed.items[1].tokens)
        # A sum over part of a session would read as the cost of the whole.
        self.assertIsNone(consumed.cost_usd)

    def test_mixed_cache_durations_leave_a_writing_block_unrated(self) -> None:
        usage = Usage(
            billed=(Billed(model="claude-opus-5", tokens=_tokens(cache_write=10), reported_cost_usd=None),),
            cache_write_duration="mixed",
        )

        consumed = consumption(CATALOGUE, Backend.CLAUDE, "claude-opus-5", usage)

        self.assertIsNone(consumed.items[0].cost_usd)
        self.assertIsNone(consumed.cost_usd)

    def test_a_session_that_reported_nothing_costs_nothing_known(self) -> None:
        consumed = consumption(
            CATALOGUE,
            Backend.CODEX,
            "gpt-5.6-sol",
            Usage(billed=(), cache_write_duration=None),
        )

        self.assertEqual((), consumed.items)
        self.assertIsNone(consumed.cost_usd)


if __name__ == "__main__":
    unittest.main()
