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

def test_emoji_in_the_middle_are_stripped_from_the_hook():
    """
    A mid-sentence emoji (not a trailing decorative run) is rarer in
    practice and still just dropped from the burned text: drawtext and
    libass cannot draw colour emoji, and left in, it renders as a hollow
    box on the first frame — the one that decides whether anyone keeps
    watching.
    """
    hook, emoji = shortsmith.hook_text("When 😭 your newest employee gives 110%")
    assert "😭" not in hook
    assert hook.startswith("When")
    assert emoji == ""  # not a trailing run, so nothing to composite either


def test_a_trailing_emoji_is_split_off_rather_than_dropped():
    """
    A trailing run is how titles in this pipeline actually carry decorative
    emoji, and it gets composited as an image alongside the text instead of
    being thrown away.
    """
    hook, emoji = shortsmith.hook_text("When your newest employee gives 110% 😭🔥")
    assert "😭" not in hook and "🔥" not in hook
    assert hook == "When your newest employee gives 110%"
    assert emoji == "😭🔥"


def test_hook_is_short_enough_to_read():
    long_title = "An extremely long title that nobody could possibly read in one second flat"
    hook, _ = shortsmith.hook_text(long_title)
    assert len(hook) <= shortsmith.HOOK_MAX_CHARS + 1  # +1 for the ellipsis


def test_hook_truncates_on_a_word_boundary():
    hook, _ = shortsmith.hook_text("alpha bravo charlie delta echo foxtrot golf hotel india")
    assert "…" in hook
    # No half-words before the ellipsis.
    assert not hook.replace("…", "").endswith(("alph", "brav", "charli"))


def test_truncation_drops_the_trailing_emoji_too():
    """
    A hook cut short lost its actual ending; tacking the emoji onto a
    truncated, unrelated word would read as a mistake rather than a title.
    """
    long_title = ("An extremely long title that nobody could possibly read "
                  "in one second flat 🎉🔥")
    hook, emoji = shortsmith.hook_text(long_title)
    assert hook.endswith("…")
    assert emoji == ""


def test_whitespace_is_collapsed():
    assert shortsmith.hook_text("  too    many   spaces ") == ("too many spaces", "")


def test_short_title_is_left_alone():
    assert shortsmith.hook_text("Short and fine") == ("Short and fine", "")


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


# --- fitting the hook to the frame -----------------------------------------
#
# A character-count wrap is a proxy for a pixel budget, and the two part
# company as soon as the text is wide-glyphed. "Florida Lawn Care" is
# seventeen characters, inside a twenty-one character wrap, and rendered
# 722px wide in a 720px frame -- running off both edges on the one frame
# that has to work.

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

pytestmark_font = pytest.mark.skipif(
    not __import__("pathlib").Path(FONT).exists(), reason="DejaVu not installed")


def _width_of(text, px):
    from PIL import ImageFont
    return ImageFont.truetype(FONT, px).getlength(text)


@pytestmark_font
@pytest.mark.parametrize("text", [
    "Florida Lawn Care Accordion Spell Backfires",
    "WHEN YOUR LANDLORD SEES THE FLOOD",
    "MMMMMMMMMMMMMMMMMMM WWWWW",
    "Short one",
])
def test_no_line_exceeds_the_frame(text):
    lines, px = shortsmith.fit_hook(text, 720, 1280, FONT)
    limit = 720 * shortsmith.HOOK_WIDTH_FRACTION
    assert lines
    for line in lines:
        assert _width_of(line, px) <= limit, f"{line!r} overflows at {px}px"


@pytestmark_font
def test_the_regression_case_specifically():
    """The exact string that overflowed, at the exact frame size."""
    lines, px = shortsmith.fit_hook("Florida Lawn Care Accordion Spell", 720, 1280, FONT)
    assert all(_width_of(line, px) <= 720 for line in lines)


@pytestmark_font
def test_an_unbreakable_word_shrinks_the_font_rather_than_clipping():
    """No wrap can save one long word, so the type comes down first."""
    lines, px = shortsmith.fit_hook("Supercalifragilisticexpialidocious", 720, 1280, FONT)
    assert px < 1280 // shortsmith.HOOK_FONT_DIVISOR
    assert len(lines) <= 2


@pytestmark_font
def test_the_font_never_shrinks_below_readable():
    _, px = shortsmith.fit_hook("W" * 60, 720, 1280, FONT)
    assert px >= shortsmith.HOOK_MIN_FONT_PX


@pytestmark_font
def test_a_wider_frame_keeps_the_hook_on_one_line():
    """Wrapping should respond to the frame, not to a fixed character count."""
    narrow, _ = shortsmith.fit_hook("Florida Lawn Care", 720, 1280, FONT)
    wide, _ = shortsmith.fit_hook("Florida Lawn Care", 1080, 1920, FONT)
    assert len(wide) <= len(narrow)


def test_unmeasurable_font_falls_back_to_a_narrow_wrap():
    """A narrower hook than necessary beats one that runs off the frame."""
    lines, px = shortsmith.fit_hook(
        "Florida Lawn Care Accordion Spell", 720, 1280, font_path=None)
    assert lines
    assert all(len(line) <= shortsmith.HOOK_WRAP_WIDTH + 1 for line in lines)
    assert px > 0


def test_missing_video_size_does_not_raise():
    assert shortsmith.video_size("/nonexistent/file.mp4") is None


# --- what the first published clip off this path got wrong -----------------
#
# All three shipped in one eight-second clip: the hook sat over the punchline
# for the full duration, its three lines hung against the left edge, and
# Mímir read the caption's hashtags aloud. None of them raised, and the suite
# was green -- they were only visible in the finished video.

def test_narration_does_not_read_hashtags_aloud():
    """A caption is written to be read under a Short, not spoken."""
    b = _brief(caption="Unboxing the gadget hit different #aislop #comedy")
    assert shortsmith.narration_line(b) == "Unboxing the gadget hit different"


def test_narration_drops_emoji_rather_than_naming_them():
    b = _brief(caption="A box \U0001F4E6 of legs \U0001F9B6")
    assert shortsmith.narration_line(b) == "A box of legs"


def test_an_emoji_glued_to_a_hashtag_takes_the_hashtag_with_it():
    """
    Captions arrive as "...different 📦🦶#aislop" with no space, so the
    emoji strip has to run first or the hashtag survives as a bare word.
    """
    b = _brief(caption="hit different \U0001F4E6\U0001F9B6#aislop")
    assert shortsmith.narration_line(b) == "hit different"


def test_the_hook_clears_before_the_clip_ends():
    """
    An eight-second Short is setup / turn / escalation. A hook with no time
    limit covers the payoff it was written to sell.
    """
    assert 0 < shortsmith.HOOK_HOLD_SEC < 8
    assert 0 < shortsmith.HOOK_FADE_SEC < shortsmith.HOOK_HOLD_SEC


# --- compositing the trailing emoji as an image -----------------------------
#
# libass and drawtext still cannot draw colour emoji, so a trailing run is
# rendered separately via agent.text_fit and composited with ffmpeg's
# overlay filter instead. No ffmpeg runs here, same as the rest of this
# file — what is worth guarding is the pure-Python layout math, which is
# invisible until the composited frame is wrong.

from agent import text_fit

pytestmark_emoji_font = pytest.mark.skipif(
    not text_fit.emoji_font_file(), reason="no colour emoji font on this machine")


def test_split_trailing_emoji_separates_text_and_emoji():
    text, emoji = text_fit.split_trailing_emoji(
        "Broth-Spraying Plastic Parrot Bounces Into Electric Toaster 🦜🍲")
    assert text == "Broth-Spraying Plastic Parrot Bounces Into Electric Toaster"
    assert emoji == "🦜🍲"


def test_split_trailing_emoji_leaves_plain_text_alone():
    assert text_fit.split_trailing_emoji("No emoji here at all") == (
        "No emoji here at all", "")


def test_split_trailing_emoji_ignores_a_leading_or_interior_run():
    """
    Only a trailing run is recognised — the shape titles in this pipeline
    actually use. Leading/interior emoji still fall to strip_emoji
    downstream, same as before this existed.
    """
    text, emoji = text_fit.split_trailing_emoji("🎉 Cats are back")
    assert emoji == ""
    assert "🎉" in text  # untouched here; hook_text's strip_emoji drops it later


@pytestmark_emoji_font
def test_emoji_glyph_renders_real_colour_pixels():
    result = text_fit.emoji_glyph("🦜", 68)
    assert result is not None
    img, advance = result
    assert advance > 0
    assert img.mode == "RGBA"
    px = img.load()
    # At least one pixel actually has colour and isn't fully transparent —
    # a hollow-box/tofu render would fail this.
    assert any(px[x, y][3] > 0 and px[x, y][:3] != (0, 0, 0)
               for x in range(img.width) for y in range(img.height))


@pytestmark_emoji_font
def test_emoji_glyph_scales_to_the_target_size():
    small, _ = text_fit.emoji_glyph("🦜", 40)
    large, _ = text_fit.emoji_glyph("🦜", 120)
    assert small.height < large.height


def test_emoji_glyph_is_none_without_a_font(monkeypatch):
    monkeypatch.setattr(text_fit, "emoji_font_file", lambda: None)
    assert text_fit.emoji_glyph("🦜", 68) is None


@pytestmark_emoji_font
@pytestmark_font
def test_trailing_emoji_is_placed_beside_the_last_line(tmp_path):
    png_path, pos, last_line_x = shortsmith._place_trailing_emoji(
        "🦜🍲", "Electric Toaster", font=FONT, font_px=68, frame_w=1080,
        y=200.0, out_dir=tmp_path, clip_id="c1")
    assert png_path is not None and png_path.exists()
    assert last_line_x is not None and 0 <= last_line_x < 1080
    ex, ey = pos
    # The emoji sits to the right of where the (re-centred) text starts.
    assert ex > last_line_x


def test_trailing_emoji_is_dropped_if_no_font_is_available(monkeypatch, tmp_path):
    """The bare text is the worst acceptable outcome, not a crop off-frame."""
    monkeypatch.setattr(text_fit, "emoji_font_file", lambda: None)
    png_path, pos, last_line_x = shortsmith._place_trailing_emoji(
        "🦜🍲", "Electric Toaster", font=None, font_px=68, frame_w=1080,
        y=200.0, out_dir=tmp_path, clip_id="c2")
    assert png_path is None and pos is None and last_line_x is None
