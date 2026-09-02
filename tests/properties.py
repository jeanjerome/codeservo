"""What every property in this suite runs under, and the values they draw from.

A property states something about every input of a shape rather than about the
handful a test author thought of, which is what a parsing boundary needs: the
inputs that break one are the inputs nobody wrote down.

Two settings follow from this suite being a gate rather than a laboratory. A
gate is reproducible, so `derandomize` makes Hypothesis derive its seed from
the test instead of from a clock, and the same tree gives the same verdict. And
a gate writes nothing into the tree it measures, so the example database is off
and Hypothesis's own cache is moved out of the working directory, where it is
otherwise created whatever the database setting says.

`deadline` is off because these properties reach the filesystem, and a wall
clock decides how long that takes on a loaded machine. A slow example would
otherwise fail a gate for a reason the tree does not hold.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from hypothesis import HealthCheck, configuration, settings
from hypothesis import strategies as st

configuration.set_hypothesis_home_dir(
    str(Path(tempfile.gettempdir()) / "codeservo-hypothesis")
)

settings.register_profile(
    "codeservo",
    derandomize=True,
    database=None,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
settings.load_profile("codeservo")


# --- Values a parser is handed --------------------------------------------

JSON_SCALARS = (
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=16)
)

KEYS = st.text(max_size=8)


def json_documents(max_leaves: int = 12) -> st.SearchStrategy[Any]:
    """Anything `json.loads` can return, nested a few levels deep."""
    return st.recursive(
        JSON_SCALARS,
        lambda children: st.lists(children, max_size=4)
        | st.dictionaries(KEYS, children, max_size=4),
        max_leaves=max_leaves,
    )


def json_objects(max_leaves: int = 12) -> st.SearchStrategy[dict]:
    """A JSON object, which is what every document of a run is."""
    return st.dictionaries(KEYS, json_documents(max_leaves), max_size=6)


# --- Ways of naming a file that is not the one a directory holds -----------

# How far above a directory a generated location reaches. A test placing
# a file to be found writes one at each of those levels.
CLIMBS = 3

DOT_NOISE = st.lists(st.just("."), max_size=3)


@st.composite
def climbing_locations(draw: st.DrawFn, name: str) -> str:
    """A relative location that leaves the directory it is resolved against.

    One to three levels up, spelled with and without the segments that
    normalise away: a check refusing `../name` while accepting `././../name`
    refuses a spelling rather than an escape.
    """
    noise = "".join(f"{segment}/" for segment in draw(DOT_NOISE))
    climbs = "../" * draw(st.integers(min_value=1, max_value=CLIMBS))
    return f"{noise}{climbs}{name}"
