"""The model catalogue: what a run may name, and what its tokens are rated at."""

import tempfile
import unittest
from pathlib import Path

from codeservo.actuators.catalogue import (
    Backend,
    CatalogueError,
    Effort,
    Price,
    load_catalogue,
    rate,
    read_catalogue,
)

VALID = '''version = 1
priced_at = "2026-09-03"
basis = "test"

[[model]]
backend = "claude"
id = "claude-opus-5"
positioning = "complete"
source = "https://example.test/pricing"
[model.price_per_million_tokens]
input = 5.0
cached_input = 0.5
output = 25.0
cache_write = { "5m" = 6.25, "1h" = 10.0 }

[[model]]
backend = "codex"
id = "gpt-5.6-sol"
positioning = "complete"
[model.price_per_million_tokens]
input = 4.0
cached_input = 0.4
output = 20.0
cache_write = { default = 5.0 }

[[model]]
backend = "codex"
id = "gpt-unpriced"
positioning = "listed without a price"
'''


def _read(text: str):
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp, "models.toml")
        path.write_text(text, encoding="utf-8")
        return read_catalogue(path)


class ReadingTests(unittest.TestCase):
    def test_reads_every_model_with_its_backend_and_price(self) -> None:
        catalogue = _read(VALID)

        self.assertEqual(1, catalogue.version)
        self.assertEqual("2026-09-03", catalogue.priced_at)
        self.assertEqual(
            ["claude-opus-5", "gpt-5.6-sol", "gpt-unpriced"],
            [model.id for model in catalogue.models],
        )
        opus = catalogue.lookup(Backend.CLAUDE, "claude-opus-5")
        self.assertEqual(
            Price(input=5.0, cached_input=0.5, cache_write={"5m": 6.25, "1h": 10.0}, output=25.0),
            opus.price,
        )
        self.assertEqual("https://example.test/pricing", opus.source)
        self.assertIsNone(catalogue.lookup(Backend.CODEX, "gpt-unpriced").price)

    def test_the_published_catalogue_reads_and_names_both_backends(self) -> None:
        catalogue = load_catalogue()

        self.assertTrue(catalogue.models_for(Backend.CLAUDE))
        self.assertTrue(catalogue.models_for(Backend.CODEX))
        for model in catalogue.models:
            with self.subTest(model=model.id):
                self.assertIsNotNone(model.price, "a published model carries a price")
                self.assertTrue(model.source, "a published price names its source")
        self.assertEqual(["low", "medium", "high", "xhigh"], list(Effort))

    def test_a_model_of_the_other_backend_is_refused_as_such(self) -> None:
        catalogue = _read(VALID)

        with self.assertRaisesRegex(CatalogueError, "a claude model and cannot be driven by codex"):
            catalogue.lookup(Backend.CODEX, "claude-opus-5")

    def test_a_model_nobody_lists_is_refused_naming_what_is_listed(self) -> None:
        catalogue = _read(VALID)

        with self.assertRaisesRegex(CatalogueError, "names no claude model 'opus'"):
            catalogue.lookup(Backend.CLAUDE, "opus")

    def test_refuses_a_catalogue_that_is_not_readable_by_name(self) -> None:
        cases = {
            "not toml": "version = [",
            "no version": '[[model]]\nbackend = "claude"\nid = "x"\npositioning = "p"\n',
            "no model": "version = 1\n",
            "unknown backend": 'version = 1\n[[model]]\nbackend = "gemini"\nid = "x"\npositioning = "p"\n',
            "twice": (
                'version = 1\n[[model]]\nbackend = "claude"\nid = "x"\npositioning = "p"\n'
                '[[model]]\nbackend = "claude"\nid = "x"\npositioning = "p"\n'
            ),
            "negative price": (
                'version = 1\n[[model]]\nbackend = "claude"\nid = "x"\npositioning = "p"\n'
                '[model.price_per_million_tokens]\ninput = -1\ncached_input = 0\noutput = 1\n'
                'cache_write = { default = 1 }\n'
            ),
            "no write price": (
                'version = 1\n[[model]]\nbackend = "claude"\nid = "x"\npositioning = "p"\n'
                '[model.price_per_million_tokens]\ninput = 1\ncached_input = 0\noutput = 1\n'
            ),
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(CatalogueError):
                    _read(text)

    def test_an_absent_file_is_refused_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(CatalogueError, "not readable"):
                read_catalogue(Path(temp, "absent.toml"))


class RatingTests(unittest.TestCase):
    """The list cost of what a stream reported, category by category."""

    OPUS = Price(input=5.0, cached_input=0.5, cache_write={"5m": 6.25, "1h": 10.0}, output=25.0)
    SOL = Price(input=4.0, cached_input=0.4, cache_write={"default": 5.0}, output=20.0)

    def test_rates_a_real_session_to_the_cent_the_backend_itself_computed(self) -> None:
        """A Claude Code session of 2026-09-03, whose costUSD was 2.471291."""
        tokens = {"input": 92, "cached_input": 2297822, "cache_write": 72277, "output": 23966}

        self.assertEqual(2.471291, rate(self.OPUS, tokens, "1h"))

    def test_rates_a_backend_naming_no_duration_at_its_one_write_price(self) -> None:
        tokens = {"input": 100000, "cached_input": 0, "cache_write": 100000, "output": 0}

        self.assertEqual(0.9, rate(self.SOL, tokens, None))

    def test_a_duration_the_table_has_no_line_for_leaves_the_cost_unknown(self) -> None:
        tokens = {"input": 0, "cached_input": 0, "cache_write": 10, "output": 0}

        self.assertIsNone(rate(self.OPUS, tokens, "mixed"))
        self.assertIsNone(rate(self.OPUS, tokens, None))

    def test_no_cache_writes_need_no_duration(self) -> None:
        tokens = {"input": 1000000, "cached_input": 0, "cache_write": 0, "output": 0}

        self.assertEqual(5.0, rate(self.OPUS, tokens, None))

    def test_a_count_the_stream_did_not_carry_leaves_the_cost_unknown(self) -> None:
        for missing in ("input", "cached_input", "cache_write", "output"):
            with self.subTest(missing=missing):
                tokens = {"input": 1, "cached_input": 1, "cache_write": 0, "output": 1}
                tokens[missing] = None

                self.assertIsNone(rate(self.SOL, tokens, None))

    def test_reasoning_tokens_are_not_rated_twice(self) -> None:
        tokens = {"input": 0, "cached_input": 0, "cache_write": 0, "output": 1000000, "reasoning": 500000}

        self.assertEqual(20.0, rate(self.SOL, tokens, None))


if __name__ == "__main__":
    unittest.main()
