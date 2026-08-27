"""
The demo script and the demo beats have to say the same thing.

They drifted once and nobody noticed: DEMO_SCRIPT.md predated the scoreboard
and the trend loop, demo_beats.py predated neither, and the capture is driven
by the beats — so the prose a human reads before recording described a demo
the machine would not produce. Nothing failed, because nothing checked.

The runtime cap is the other half. Devpost's three minutes is a
disqualification threshold rather than a style guideline, so going over is not
a thing to discover while editing footage.
"""

import re
from pathlib import Path

import pytest

import demo_beats as d

SCRIPT = Path(__file__).resolve().parent.parent / "DEMO_SCRIPT.md"
CAP_SEC = 180.0

# The prose wraps narration across lines inside a blockquote. Comparing text
# means undoing the wrapping, not reproducing it.
_QUOTE_RE = re.compile(r"^((?:>.*\n)+)", re.MULTILINE)


def _normalise(text: str) -> str:
    return " ".join(text.replace("—", "—").split())


def _script_quotes() -> list[str]:
    body = SCRIPT.read_text(encoding="utf-8")
    return [_normalise(m.group(1).replace(">", " ")) for m in _QUOTE_RE.finditer(body)]


def test_every_beat_appears_in_the_script():
    quotes = _script_quotes()
    for beat in d.BEATS:
        wanted = _normalise(beat.narration)
        assert wanted in quotes, (
            f"beat {beat.key!r} is not in DEMO_SCRIPT.md verbatim — "
            f"re-render the script from demo_beats.py")


def test_the_script_says_nothing_the_beats_do_not():
    """Prose-only narration is the direction the drift went last time."""
    spoken = {_normalise(b.narration) for b in d.BEATS}
    for quote in _script_quotes():
        assert quote in spoken, (
            f"DEMO_SCRIPT.md contains narration no beat produces: {quote[:70]!r}")


def test_the_demo_fits_inside_the_cap():
    assert d.estimated_runtime_sec() < CAP_SEC


def test_the_cap_holds_at_a_slower_delivery():
    """
    Nobody narrates a demo at exactly the estimate. 150 words per minute is a
    relaxed pace and the floors in min_seconds only push the total up, so this
    is the number that actually has to clear.

    This missed TAIL_PAD_SEC once: demo_assemble.py pads every segment by
    that much regardless of speech length, so a real assembled cut always
    runs len(BEATS) * TAIL_PAD_SEC longer than this sum alone predicted —
    the gap that let this test pass while the real render came in over cap.

    If the real render and this test ever disagree by a constant again,
    that constant is a duration knob demo_assemble.py added and this test
    didn't import. Add it the same way TAIL_PAD_SEC is added below, not as
    a second hardcoded number here — a restated constant is exactly what
    drifted last time.
    """
    from demo_assemble import TAIL_PAD_SEC

    total = sum(max(b.min_seconds, len(b.narration.split()) / 2.5) for b in d.BEATS)
    total += len(d.BEATS) * TAIL_PAD_SEC
    assert total < CAP_SEC, f"{total:.0f}s at a slow pace, cap is {CAP_SEC:.0f}s"


def test_beat_keys_are_unique():
    keys = [b.key for b in d.BEATS]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("beat", d.BEATS, ids=lambda b: b.key)
def test_a_beat_is_either_driven_or_declared_manual(beat):
    """
    A beat with neither actions nor a manual note captures a static page for
    its whole duration, which is how a dead thirty seconds gets into a
    three-minute video.
    """
    assert beat.actions or beat.manual, (
        f"beat {beat.key!r} has no actions and is not marked manual")


@pytest.mark.parametrize("beat", d.BEATS, ids=lambda b: b.key)
def test_scroll_to_targets_are_real_selectors(beat):
    """
    scroll_to exists specifically so a moved target fails loudly instead of
    silently scrolling the wrong distance — an empty or non-string argument
    would defeat that by not being a selector at all.
    """
    for verb, arg in beat.actions:
        if verb == "scroll_to":
            assert isinstance(arg, str) and arg.strip(), (
                f"beat {beat.key!r} has a scroll_to with no real selector: {arg!r}")
