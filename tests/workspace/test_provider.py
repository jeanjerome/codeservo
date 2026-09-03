"""The port every execution provider answers, and how one is loaded by name."""

import unittest
from pathlib import Path

from codeservo.workspace import mise, pixi, provider
from codeservo.workspace.provider import ProviderError


class PortTests(unittest.TestCase):
    def test_names_the_providers_and_the_lockfile_each_one_reads(self) -> None:
        self.assertEqual(("pixi", "mise"), provider.provider_names())
        self.assertEqual("pixi.lock", provider.lockfile_of("pixi"))
        self.assertEqual("mise.lock", provider.lockfile_of("mise"))
        with self.assertRaisesRegex(ProviderError, "conda"):
            provider.lockfile_of("conda")

    def test_loads_each_provider_by_name(self) -> None:
        state_root = Path("/state")

        self.assertIsInstance(provider.load_provider("pixi", state_root), pixi.Pixi)
        loaded = provider.load_provider("mise", state_root)
        self.assertIsInstance(loaded, mise.Mise)
        # A provider keeping its tools outside every tree keeps them in the
        # controller's state directory, under its own name.
        self.assertEqual(state_root / "providers" / "mise", loaded.data_dir)
        with self.assertRaisesRegex(ProviderError, "conda"):
            provider.load_provider("conda", state_root)

    def test_each_provider_says_where_it_installs(self) -> None:
        # In the tree it measures, so each candidate is installed after its
        # checkout; or outside every tree, so the controller installs once
        # before the baseline.
        self.assertFalse(pixi.Pixi().shared_installs)
        self.assertTrue(mise.Mise(Path("/state/providers/mise")).shared_installs)
        self.assertEqual("pixi", pixi.Pixi().name)
        self.assertEqual("mise", mise.Mise(Path("/state/providers/mise")).name)

    def test_quotes_a_value_for_the_shell(self) -> None:
        self.assertEqual("'plain'", provider.quote("plain"))
        self.assertEqual("'it'\\''s'", provider.quote("it's"))
        self.assertEqual("'$HOME'", provider.quote("$HOME"))


if __name__ == "__main__":
    unittest.main()
