"""
Unit tests for finishing a generated clip.

No ffmpeg runs and no TTS is called. What is worth guarding is the text
preparation, because both failure modes are visible in the finished video
and neither raises: an emoji rendered as a hollow box on the one frame that
has to work, or a hook too long to read in the second it gets.
"""

import pytest

from agent import shortsmith
from agent.trend_loop import Brief


def _brief(title="A perfectly ordinary title", caption="A caption."):
    return Brief(topic="funny", angle="deadpan", video_prompt="",
                 title=title, caption=caption)


# --- the hook --------------------------------------------------------------

def test_emoji_are_stripped_from_the_hook():
    """
    drawtext and libass cannot render colour emoji, and a model writing
    titles for a comedy channel puts them in constantly. Left in, they draw
    as hollow boxes on the first frame — the one that decides whether anyone
    keeps watching.
    """
    hook = shortsmith.hook_text("When your newest employee gives 110% 😭🔥")
    assert "😭" not in hook and "🔥" not in hook
    assert hook.startswith("When your newest employee")


def test_hook_is_short_enough_to_read():
    long_title = "An extremely long title that nobody could possibly read in one second flat"
    hook = shortsmith.hook_text(long_title)
    assert len(hook) <= shortsmith.HOOK_MAX_CHARS + 1  # +1 for the ellipsis


def test_hook_truncates_on_a_word_boundary():
    hook = shortsmith.hook_text("alpha bravo charlie delta echo foxtrot golf hotel india")
    assert "…" in hook
    # No half-words before the ellipsis.
    assert not hook.replace("…", "").endswith(("alph", "brav", "charli"))


def test_whitespace_is_collapsed():
    assert shortsmith.hook_text("  too    many   spaces ") == "too many spaces"


def test_short_title_is_left_alone():
    assert shortsmith.hook_text("Short and fine") == "Short and fine"


# --- the spoken line -------------------------------------------------------

def test_narration_prefers_the_caption_over_the_title():
    """
    The angle is a production note written for a generator; reading it aloud
    describes the joke instead of telling it.
    """
    b = _brief(title="A title", caption="The spoken line.")
    assert shortsmith.narration_line(b) == "The spoken line."


def test_narration_falls_back_to_the_title():
    b = _brief(title="Only a title", caption="")
    assert shortsmith.narration_line(b) == "Only a title"


def test_narration_is_capped_to_what_fits_in_eight_seconds():
    b = _brief(caption=" ".join(f"word{i}" for i in range(80)))
    assert len(shortsmith.narration_line(b).split()) <= 22


def test_empty_brief_yields_no_narration():
    b = _brief(title="", caption="")
    assert shortsmith.narration_line(b) == ""


# --- ffmpeg escaping -------------------------------------------------------

@pytest.mark.parametrize("raw,forbidden", [
    ("a:b", ":"),
    ("it's here", "'"),
    ("100% sure", "%"),
])
def test_drawtext_special_characters_are_neutralised(raw, forbidden):
    """An unescaped colon or quote breaks the whole filter string."""
    escaped = shortsmith._escape(raw)
    assert f"\\{forbidden}" in escaped or forbidden not in escaped


def test_backslashes_are_escaped_first():
    assert shortsmith._escape("a\\b").startswith("a\\\\")
