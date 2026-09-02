"""What reading a constitution must hold for every file, not only for ours.

The constitution is a control input the controller reads before anything else
runs. A file it cannot make sense of has exactly one honest outcome: a refusal
naming what is wrong. An interpreter traceback is not that, and it ends the run
somewhere no decision was recorded.
"""

import tempfile
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from codeservo.domain.constitution import Constitution
from codeservo.policies.constitution import ConstitutionError, load_constitution
from properties import JSON_SCALARS

VALID = """version = 1

[scope]
protected = [".codeservo/**"]
max_changed_files = 5
max_diff_lines = 100

[[gate]]
name = "unit"
phase = "quick"
command = "true"
timeout_seconds = 60
baseline = true

[[gate]]
name = "compile"
phase = "full"
command = "true"
timeout_seconds = 60
baseline = true

[review]
blocking_severities = ["blocker"]
"""

# Every scalar a TOML document can carry where a reader expects another type.
TOML_SCALARS = st.one_of(
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.booleans(),
    st.text(max_size=12),
    st.lists(st.integers(), max_size=2),
)


def _toml(value: object) -> str:
    """One drawn value, spelled the way TOML spells it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_toml(item) for item in value) + "]"
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return repr(value)


class ReadingProperties(unittest.TestCase):
    """A constitution is read, or refused by name. There is no third answer."""

    def read(self, text: str) -> None:
        """Read one constitution text, letting only the named refusal through."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".codeservo").mkdir()
            constitution = repo / ".codeservo" / "constitution.toml"
            constitution.write_text(text, encoding="utf-8")
            try:
                result = load_constitution(repo)
            except ConstitutionError:
                return
            self.assertIsInstance(result, Constitution)

    @given(text=st.text(max_size=96))
    def test_any_text_is_read_or_refused_by_name(self, text):
        self.read(text)

    @given(key=st.sampled_from(["protected", "max_changed_files", "max_diff_lines"]),
           value=TOML_SCALARS)
    def test_any_scope_value_is_read_or_refused_by_name(self, key, value):
        lines = [
            f"{key} = {_toml(value)}" if line.startswith(f"{key} ") else line
            for line in VALID.splitlines()
        ]
        self.read("\n".join(lines) + "\n")

    @given(
        key=st.sampled_from(
            ["name", "phase", "command", "timeout_seconds", "baseline"]
        ),
        value=TOML_SCALARS,
    )
    def test_any_gate_value_is_read_or_refused_by_name(self, key, value):
        lines = [
            f"{key} = {_toml(value)}" if line.startswith(f"{key} ") else line
            for line in VALID.splitlines()
        ]
        self.read("\n".join(lines) + "\n")

    @given(key=st.sampled_from(["name", "phase", "command", "timeout_seconds"]))
    def test_a_gate_missing_any_field_is_refused_by_name(self, key):
        lines = [
            line for line in VALID.splitlines() if not line.startswith(f"{key} ")
        ]
        self.read("\n".join(lines) + "\n")

    @given(value=JSON_SCALARS)
    def test_any_review_policy_is_read_or_refused_by_name(self, value):
        if isinstance(value, float) and value != value:
            return
        lines = [
            f"blocking_severities = {_toml(value)}"
            if line.startswith("blocking_severities ")
            else line
            for line in VALID.splitlines()
        ]
        self.read("\n".join(lines) + "\n")


if __name__ == "__main__":
    unittest.main()
