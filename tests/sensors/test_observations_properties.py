"""What classifying a gate's document must hold for every byte string.

The document arrives from a gate the controller did not write, so its bytes are
whatever that gate produced. Classification is the boundary where they become
one of four words; nothing before it is trusted, and nothing after it may be a
traceback.
"""

import json
import unittest

from hypothesis import given
from hypothesis import strategies as st

from codeservo.sensors.observations import (
    SCHEMA_VERSION,
    Classification,
    Severity,
    Status,
    classify,
)
from properties import json_documents

FINDINGS = st.lists(
    st.fixed_dictionaries(
        {
            "id": st.text(min_size=1, max_size=8),
            "severity": st.sampled_from(list(Severity)).map(str),
            "path": st.none() | st.text(max_size=8),
            "line": st.none() | st.integers(min_value=1, max_value=999),
            "message": st.text(min_size=1, max_size=16),
        }
    ),
    max_size=3,
)

METRICS = st.dictionaries(
    st.text(max_size=6),
    st.integers() | st.floats(allow_nan=False, allow_infinity=False),
    max_size=3,
)


@st.composite
def observations(draw: st.DrawFn) -> dict:
    """A document carrying exactly the six fields the contract names."""
    return {
        "schema_version": SCHEMA_VERSION,
        "sensor": draw(st.text(min_size=1, max_size=12)),
        "status": str(draw(st.sampled_from(list(Status)))),
        "summary": draw(st.text(max_size=24)),
        "findings": draw(FINDINGS),
        "metrics": draw(METRICS),
    }


class ClassificationProperties(unittest.TestCase):
    """Four words, and never a fifth answer."""

    @given(raw=st.binary(max_size=128), passed=st.booleans())
    def test_any_bytes_reach_a_classification(self, raw, passed):
        status, error = classify(raw, passed=passed)
        self.assertIn(status, set(Classification))
        self.assertIsInstance(error, str | None)

    @given(document=json_documents(), passed=st.booleans())
    def test_any_json_document_reaches_a_classification(self, document, passed):
        raw = json.dumps(document).encode("utf-8")
        status, _ = classify(raw, passed=passed)
        self.assertIn(status, set(Classification))

    @given(document=observations(), passed=st.booleans())
    def test_a_document_of_the_contract_is_never_invalid(self, document, passed):
        """A document built to the contract is read; only its verdict is judged.

        Whether it agrees with the exit code is a separate statement, and the
        one the classification is for. What it may never be is `invalid`, which
        would say the contract refuses a document the contract describes.
        """
        raw = json.dumps(document).encode("utf-8")
        status, _ = classify(raw, passed=passed)
        self.assertNotEqual(status, Classification.INVALID)
        agrees = document["status"] == (Status.PASSED if passed else Status.FAILED)
        self.assertEqual(
            status,
            Classification.VALID if agrees else Classification.CONTRADICTED,
        )

    @given(document=observations(), extra=st.text(min_size=1, max_size=6))
    def test_a_seventh_field_is_refused(self, document, extra):
        """The contract carries exactly six fields, so a seventh is not one."""
        if extra in document:
            return
        document[extra] = None
        raw = json.dumps(document).encode("utf-8")
        status, error = classify(raw, passed=True)
        self.assertEqual(status, Classification.INVALID)
        self.assertIsInstance(error, str)


if __name__ == "__main__":
    unittest.main()
