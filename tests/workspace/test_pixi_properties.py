"""What reading a provider's description must hold for every document.

The description is another program's output, and the controller reads it
before anything is measured. A document it cannot make sense of has exactly
one honest outcome: a refusal naming what is wrong. An interpreter traceback
is not that, and it ends the run before any decision was recorded — in the
one component whose business is closing a run with a decision.
"""

import json
import unittest

from hypothesis import given
from hypothesis import strategies as st

from codeservo.workspace.pixi import Description, ProviderError, read_description
from properties import json_documents

ENVIRONMENT = "default"

DESCRIBED = {
    "version": "0.77.1",
    "platform": "osx-arm64",
    "environments_info": [
        {
            "name": ENVIRONMENT,
            "tasks": ["lint", "test"],
            "prefix": "/tree/.pixi/envs/default",
        }
    ],
}


class DescriptionProperties(unittest.TestCase):
    """A description is read, or refused by name. There is no third answer."""

    def read(self, stdout: str) -> None:
        """Read one description, letting only the named refusal through.

        What comes back is held to the shape a record carries: the three
        strings are strings and the task set is a set of them, whatever the
        provider printed. A value of another type rendered into the record
        would state something no measurement produced.
        """
        try:
            described = read_description(
                stdout, manifest_name="pyproject.toml", environment=ENVIRONMENT
            )
        except ProviderError:
            return
        self.assertIsInstance(described, Description)
        self.assertIsInstance(described.version, str)
        self.assertIsInstance(described.platform, str)
        self.assertIsInstance(described.prefix, str)
        self.assertIsInstance(described.tasks, tuple)
        for task in described.tasks:
            self.assertIsInstance(task, str)

    @given(text=st.text(max_size=64))
    def test_any_text_is_read_or_refused_by_name(self, text):
        self.read(text)

    @given(document=json_documents())
    def test_any_document_is_read_or_refused_by_name(self, document):
        self.read(json.dumps(document))

    @given(
        key=st.sampled_from(["version", "platform", "environments_info"]),
        value=json_documents(),
    )
    def test_any_value_where_a_description_carries_one(self, key, value):
        self.read(json.dumps({**DESCRIBED, key: value}))

    @given(
        key=st.sampled_from(["name", "tasks", "prefix"]),
        value=json_documents(),
    )
    def test_any_value_where_an_environment_carries_one(self, key, value):
        described = {**DESCRIBED["environments_info"][0], key: value}
        self.read(json.dumps({**DESCRIBED, "environments_info": [described]}))


if __name__ == "__main__":
    unittest.main()
