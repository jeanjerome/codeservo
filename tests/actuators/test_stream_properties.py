"""What a backend's event stream must yield, whatever the backend wrote.

A stream is the one control input no gate produced and no schema constrains:
another program writes it, a version of that program moves its field names,
and a killed session truncates it mid-line. The adapter reading it is the
boundary between that and the record.

Two things are stated here for both backends. Reading a stream reaches an
answer rather than an interpreter traceback, because a traceback ends the run
with the actuation already applied and nothing recorded about it. And what the
answer carries is what the record declares: a field typed `str | None` holds a
string or nothing, and every value is one JSON can carry — a stream may say
`NaN`, which `json.loads` accepts and no record can be written with.
"""

import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from codeservo.actuators import claude_code, codex
from codeservo.actuators.base import ObservedProfile
from properties import json_documents, json_objects

# What `json.loads` returns and `json.dumps` cannot write back. A stream is
# read with the standard decoder, which accepts all three as literals.
NOT_IN_JSON = st.sampled_from([float("nan"), float("inf"), float("-inf")])

STREAM_VALUES = json_documents(8) | NOT_IN_JSON

# The keys each adapter reaches for. A stream carrying one of another shape is
# the input that decides whether the adapter reports or assumes.
CLAUDE_RESULT_KEYS = (
    "session_id",
    "subtype",
    "is_error",
    "num_turns",
    "total_cost_usd",
    "terminal_reason",
    "result",
    "modelUsage",
    "usage",
)
CODEX_KEYS = ("model", "reasoning_effort", "service_tier", "msg")


@st.composite
def claude_streams(draw: st.DrawFn) -> list[dict]:
    """A stream shaped like the one Claude Code writes, valued arbitrarily.

    The two events the adapter looks for are always present, so the drawn
    values reach the fields that are read rather than being filtered out by
    an event type nothing matches.
    """
    result: dict[str, Any] = {"type": "result"}
    for key in draw(st.lists(st.sampled_from(CLAUDE_RESULT_KEYS), unique=True)):
        result[key] = draw(STREAM_VALUES)
    init = {
        "type": "system",
        "subtype": "init",
        "model": draw(STREAM_VALUES),
    }
    noise = draw(st.lists(json_objects(6), max_size=2))
    return [init, *noise, result]


@st.composite
def codex_streams(draw: st.DrawFn) -> list[dict]:
    """A stream shaped like the one Codex writes, valued arbitrarily."""
    events: list[dict] = []
    for _ in range(draw(st.integers(min_value=1, max_value=3))):
        event: dict[str, Any] = {}
        for key in draw(st.lists(st.sampled_from(CODEX_KEYS), unique=True)):
            event[key] = draw(STREAM_VALUES)
        events.append(event)
    return events


def _writable(document: object) -> None:
    """The document is one a record can be written with.

    `json.dumps` writes `NaN` and `Infinity` by default, which no other reader
    of `evidence.json` accepts. A record carrying either states a number JSON
    has no way to carry.
    """
    json.dumps(document, allow_nan=False)


class StreamReadingProperties(unittest.TestCase):
    """Any bytes on a stream are read as the events it holds, or as none."""

    def read(self, reader, data: bytes) -> list[dict]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_bytes(data)
            events = reader(path)
        self.assertIsInstance(events, list)
        for event in events:
            self.assertIsInstance(event, dict)
        return events

    @given(data=st.binary(max_size=256))
    def test_any_bytes_are_read_as_the_objects_they_hold(self, data):
        for reader in (claude_code._events, codex._events):
            self.read(reader, data)

    @given(documents=st.lists(json_documents(8), max_size=4))
    def test_a_stream_of_documents_keeps_the_objects_and_nothing_else(
        self, documents
    ):
        lines = "\n".join(json.dumps(document) for document in documents)
        events = self.read(codex._events, lines.encode("utf-8"))
        self.assertEqual(
            [document for document in documents if isinstance(document, dict)],
            events,
        )


class ObservedProfileProperties(unittest.TestCase):
    """The profile a session reported is three strings or three absences.

    Nothing is filled in from the request, so what the record shows is what
    the stream said — and what the stream said is only carried where it is
    what the field declares.
    """

    def observe(self, adapter, events: list[dict]) -> None:
        observed = adapter._observed(events)
        self.assertIsInstance(observed, ObservedProfile)
        for field in fields(ObservedProfile):
            reported = getattr(observed, field.name)
            if reported is not None:
                self.assertIsInstance(reported, str, f"{field.name}: {reported!r}")
        _writable(observed.to_document())

    @given(events=claude_streams())
    def test_a_claude_stream_reports_strings_or_nothing(self, events):
        self.observe(claude_code, events)

    @given(events=codex_streams())
    def test_a_codex_stream_reports_strings_or_nothing(self, events):
        self.observe(codex, events)

    @given(events=st.lists(json_objects(8), max_size=4))
    def test_any_objects_report_strings_or_nothing(self, events):
        for adapter in (claude_code, codex):
            self.observe(adapter, events)


class SessionProperties(unittest.TestCase):
    """What the result event says is carried as the record declares it.

    A field the event carries as something else is not a measurement this
    record can hold, so it is reported as answering nothing rather than
    written through: a consumer reading the record by its declared shape would
    otherwise find a mapping where a count belongs.
    """

    @given(events=claude_streams())
    def test_a_session_carries_only_what_it_declares(self, events):
        session = claude_code._session(claude_code._result_event(events))
        self.assertIsInstance(session.is_error, bool)
        for name in ("session_id", "subtype", "terminal_reason"):
            value = getattr(session, name)
            self.assertTrue(value is None or isinstance(value, str))
        self.assertTrue(
            session.num_turns is None or isinstance(session.num_turns, int)
        )
        self.assertTrue(
            session.total_cost_usd is None
            or isinstance(session.total_cost_usd, float)
        )
        _writable(session.to_document())

    @given(events=claude_streams())
    def test_a_usage_record_is_a_model_name_over_counts_or_nothing(self, events):
        usage = claude_code._usage(events)
        self.assertIsInstance(usage.billed, tuple)
        for billed in usage.billed:
            self.assertIsInstance(billed.model, str)
            for count in billed.tokens.to_document().values():
                self.assertTrue(count is None or isinstance(count, int))
            self.assertTrue(
                billed.reported_cost_usd is None
                or isinstance(billed.reported_cost_usd, float)
            )
        self.assertTrue(
            usage.cache_write_duration is None
            or isinstance(usage.cache_write_duration, str)
        )
        _writable(usage.to_document())


class ReviewResultProperties(unittest.TestCase):
    """A reviewer's answer is an object, or a refusal naming the file.

    The answer decides acceptance, so the one outcome that may not happen is
    the adapter handing the controller something that is not a review.
    """

    @given(data=st.binary(max_size=256))
    def test_any_bytes_are_a_review_or_a_named_refusal(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            stdout = Path(tmp) / "stdout.log"
            stderr = Path(tmp) / "stderr.log"
            stdout.write_bytes(data)
            stderr.write_text("", encoding="utf-8")
            try:
                review = claude_code._review_result(stdout, stderr)
            except claude_code.ClaudeCodeError:
                return
            self.assertIsInstance(review, dict)

    @given(payload=json_documents(10))
    def test_any_document_is_a_review_or_a_named_refusal(self, payload):
        with tempfile.TemporaryDirectory() as tmp:
            stdout = Path(tmp) / "stdout.log"
            stderr = Path(tmp) / "stderr.log"
            stdout.write_text(json.dumps(payload), encoding="utf-8")
            stderr.write_text("", encoding="utf-8")
            try:
                review = claude_code._review_result(stdout, stderr)
            except claude_code.ClaudeCodeError:
                return
            self.assertIsInstance(review, dict)


if __name__ == "__main__":
    unittest.main()
