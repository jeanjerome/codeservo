"""What a digest must survive between being written and being recomputed.

A record is hashed when it is produced and hashed again when `verify-run` reads
it back, and the two must agree. Between them the document is serialised, read
back, and has its absolute locations rewritten relative to the run directory.
Each of those is a step a digest has to come through unchanged.
"""

import json
import tempfile
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from codeservo.evidence.digests import (
    relative_evidence_paths,
    sha256_json,
    sha256_record,
)
from properties import JSON_SCALARS, json_documents

FIELD_NAMES = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=6
)


@st.composite
def flat_documents(draw: st.DrawFn) -> dict:
    """A gate result's shape: values, and locations only at the top level."""
    document = {
        key: value
        for key, value in draw(
            st.dictionaries(FIELD_NAMES, JSON_SCALARS, max_size=4)
        ).items()
        if not key.endswith("_path")
    }
    for name in draw(st.lists(FIELD_NAMES, max_size=3, unique=True)):
        document[f"{name}_path"] = f"/absolute/run/{name}.log"
    return document


class RoundTripProperties(unittest.TestCase):
    """A digest taken before writing is the digest taken after reading back."""

    @given(document=json_documents())
    def test_serialising_and_reading_back_leaves_the_digest_alone(self, document):
        before = sha256_json(document)
        after = sha256_json(json.loads(json.dumps(document)))
        self.assertEqual(before, after)


class RelativisationProperties(unittest.TestCase):
    """Where the digest survives having locations rewritten, and where it stops."""

    @given(document=flat_documents())
    def test_a_document_naming_locations_at_the_top_keeps_its_digest(self, document):
        with tempfile.TemporaryDirectory() as tmp:
            rewritten = relative_evidence_paths(document, Path(tmp))
            self.assertEqual(sha256_record(document), sha256_record(rewritten))

    @given(name=FIELD_NAMES, container=FIELD_NAMES)
    def test_a_location_named_deeper_carries_the_digest_with_it(self, name, container):
        """The limit of the invariant above, stated rather than discovered.

        `sha256_record` drops the locations a document names at its top level,
        which is why relativising them leaves the digest alone. It does not
        descend, so a location named one level down is hashed, and rewriting it
        moves the digest — a record read back would then recompute a digest
        that is not the one it carries.

        Nothing in the package names a location that deep today. This states
        where the contract stops, so that moving it is a decision and not a
        discovery.
        """
        document = {container: {f"{name}_path": "/absolute/run/deeper.log"}}
        with tempfile.TemporaryDirectory() as tmp:
            rewritten = relative_evidence_paths(document, Path(tmp))
            self.assertNotEqual(sha256_record(document), sha256_record(rewritten))


if __name__ == "__main__":
    unittest.main()
