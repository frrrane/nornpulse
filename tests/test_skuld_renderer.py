"""
Unit tests for Skuld's pure rendering logic — time parsing, caption
chunking/timing, colour interpolation, and FFmpeg filter-graph
construction.

Deliberately excludes anything that shells out to FFmpeg or calls an
API: these run in milliseconds so they're cheap to run on every change,
which is the point. Several cases here pin down bugs that were found by
live testing and fixed (caption overlap, the crop-vs-zoompan filter
choice, the oversized blurred-background canvas) so they can't silently
come back.
"""

import re

import pytest

from agent.skuld_renderer import (
    SkuldRenderer,
    _chunk_words,
    _distribute_chunk_times,
    _escape_drawtext,
    _lerp_rgb_via_hsv,
    _words_per_chunk,
    format_seconds_to_mmss,
    generate_rebased_ass_subtitle_file,
    parse_time_to_seconds,
)


# --------------------------------------------------------------------------
# Time parsing / formatting
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("00:00", 0.0),
    ("01:30", 90.0),
    ("10:05", 605.0),
    ("1:00:00", 3600.0),
    ("01:02:03", 3723.0),
])
def test_parse_time_to_seconds(text, expected):
    assert parse_time_to_seconds(text) == pytest.approx(expected)


@pytest.mark.parametrize("seconds,expected", [
    (0.0, "00:00"),
    (90.0, "01:30"),
    (605.0, "10:05"),
])
def test_format_seconds_to_mmss(seconds, expected):
    assert format_seconds_to_mmss(seconds) == expected


def test_time_round_trip_is_stable():
    for seconds in (0.0, 7.0, 63.0, 599.0, 3599.0):
        assert parse_time_to_seconds(format_seconds_to_mmss(seconds)) == pytest.approx(seconds)


# --------------------------------------------------------------------------
# Caption chunking and timing
# --------------------------------------------------------------------------

def test_words_per_chunk_shrinks_as_crazy_rises():
    """Higher 'crazy' means faster reveals, i.e. fewer words per chunk."""
    calm, wild = _words_per_chunk(0.0), _words_per_chunk(1.0)
    assert calm > wild >= 1


def test_chunk_words_preserves_every_word_in_order():
    text = "the quick brown fox jumps over the lazy dog"
    chunks = _chunk_words(text, 3)
    assert " ".join(chunks).split() == text.split()


def test_chunk_words_respects_chunk_size():
    chunks = _chunk_words("a b c d e f g", 3)
    assert all(len(c.split()) <= 3 for c in chunks)


def test_chunk_words_handles_empty_and_single():
    assert _chunk_words("", 3) == [] or _chunk_words("", 3) == [""]
    assert _chunk_words("solo", 3) == ["solo"]


def test_distribute_chunk_times_spans_window_without_overlap():
    times = _distribute_chunk_times(["alpha", "bravo", "charlie", "delta"], 10.0, 14.0)
    assert len(times) == 4
    # Each chunk starts no earlier than the previous one ends.
    for (_, prev_end), (next_start, _) in zip(times, times[1:]):
        assert next_start >= prev_end - 1e-6
    assert times[0][0] == pytest.approx(10.0)
    assert times[-1][1] == pytest.approx(14.0, abs=0.01)


def test_distribute_chunk_times_single_chunk_uses_whole_window():
    (start, end), = _distribute_chunk_times(["only"], 2.0, 5.0)
    assert start == pytest.approx(2.0)
    assert end == pytest.approx(5.0, abs=0.01)


def test_distribute_chunk_times_longer_chunks_get_more_time():
    """Timing is proportional to character count, standing in for real ASR timing."""
    (_, short_end), (long_start, long_end) = _distribute_chunk_times(["hi", "a much longer chunk"], 0.0, 10.0)
    assert (long_end - long_start) > short_end


def test_distribute_chunk_times_never_runs_past_the_line():
    """Even with many tiny chunks, the min-duration floor must be scaled back."""
    times = _distribute_chunk_times(["a"] * 20, 0.0, 1.0)
    assert times[-1][1] <= 1.0 + 1e-6


# --------------------------------------------------------------------------
# Colour handling
# --------------------------------------------------------------------------

def test_hsv_lerp_endpoints_are_exact():
    cyan, orange = (110, 210, 255), (255, 70, 20)
    assert _lerp_rgb_via_hsv(cyan, orange, 0.0) == cyan
    assert _lerp_rgb_via_hsv(cyan, orange, 1.0) == orange


def test_hsv_lerp_midpoint_stays_saturated():
    """
    The reason this uses HSV rather than a straight RGB average: cyan and
    orange-red are far enough apart in hue that lerping channelwise
    produces a washed-out grey midpoint. The accent colour has to stay
    vivid to read as an accent at all.
    """
    mid = _lerp_rgb_via_hsv((110, 210, 255), (255, 70, 20), 0.5)
    spread = max(mid) - min(mid)
    naive_rgb_mid = tuple((a + b) // 2 for a, b in zip((110, 210, 255), (255, 70, 20)))
    naive_spread = max(naive_rgb_mid) - min(naive_rgb_mid)
    assert spread > naive_spread


def test_escape_drawtext_neutralises_ffmpeg_metacharacters():
    escaped = _escape_drawtext("it's 100%: a 'test'")
    # The characters that would otherwise terminate or re-parse a
    # drawtext= argument must not survive unescaped.
    assert "\\'" in escaped or "'" not in escaped.replace("\\'", "")
    assert ":" not in escaped.replace("\\:", "")


# --------------------------------------------------------------------------
# ASS subtitle generation
# --------------------------------------------------------------------------

TRANSCRIPT = """[00:10] First line here.
[00:14] Second line follows.
[00:20] Third line much later.
"""


def _dialogue_lines(ass_text: str):
    return [ln for ln in ass_text.splitlines() if ln.startswith("Dialogue:")]


def _dialogue_times(line: str):
    parts = line.split(",")
    return parse_time_to_seconds(parts[1]), parse_time_to_seconds(parts[2])


def test_ass_only_includes_lines_overlapping_the_clip(tmp_path):
    out = generate_rebased_ass_subtitle_file(
        TRANSCRIPT, tmp_path / "s.ass", clip_start_sec=10.0, clip_end_sec=18.0,
    )
    body = out.read_text(encoding="utf-8")
    # Assert on single words, not multi-word phrases: one word per chunk
    # is wrapped in a colour override tag, so "First line" is not
    # necessarily contiguous in the output even when both words are present.
    assert "First" in body
    assert "Second" in body
    # Starts at 00:20, i.e. after the clip ends at 18s.
    assert "Third" not in body


def test_ass_is_empty_when_no_dialogue_falls_in_window(tmp_path):
    """
    A clip covering a stretch before any speech should produce zero
    caption events rather than mistimed ones — this is exactly the
    "why are there no subtitles?" case, and the answer is that it's
    correct behaviour.
    """
    out = generate_rebased_ass_subtitle_file(
        TRANSCRIPT, tmp_path / "s.ass", clip_start_sec=0.0, clip_end_sec=8.0,
    )
    assert _dialogue_lines(out.read_text(encoding="utf-8")) == []


def test_ass_timestamps_are_rebased_to_clip_start(tmp_path):
    out = generate_rebased_ass_subtitle_file(
        TRANSCRIPT, tmp_path / "s.ass", clip_start_sec=10.0, clip_end_sec=18.0,
    )
    starts = [_dialogue_times(ln)[0] for ln in _dialogue_lines(out.read_text(encoding="utf-8"))]
    # First caption must begin at (or very near) zero, not at 10s.
    assert min(starts) == pytest.approx(0.0, abs=0.05)


def test_ass_captions_never_overlap(tmp_path):
    """
    Regression test. Lines without an explicit end time used to get a
    flat guessed duration that could run past the next line's start,
    rendering two captions stacked on screen at once. The fix caps a
    guessed end at the following line's start.
    """
    fast = "\n".join(f"[00:{10 + i:02d}] line {i}" for i in range(6))
    out = generate_rebased_ass_subtitle_file(
        fast, tmp_path / "s.ass", clip_start_sec=10.0, clip_end_sec=16.0,
    )
    spans = sorted(_dialogue_times(ln) for ln in _dialogue_lines(out.read_text(encoding="utf-8")))
    for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
        assert next_start >= prev_end - 1e-6, f"captions overlap: {spans}"


def test_ass_never_emits_zero_or_negative_duration(tmp_path):
    duplicate_starts = "[00:10] one\n[00:10] two\n[00:11] three\n"
    out = generate_rebased_ass_subtitle_file(
        duplicate_starts, tmp_path / "s.ass", clip_start_sec=10.0, clip_end_sec=15.0,
    )
    for line in _dialogue_lines(out.read_text(encoding="utf-8")):
        start, end = _dialogue_times(line)
        assert end > start


def test_ass_escapes_literal_braces_from_transcript(tmp_path):
    """
    Braces are ASS override-tag syntax. A transcript containing them
    must not be able to inject styling — but the tags the renderer
    itself injects still have to work.
    """
    out = generate_rebased_ass_subtitle_file(
        "[00:10] use {b1} to embolden\n", tmp_path / "s.ass",
        clip_start_sec=10.0, clip_end_sec=14.0,
    )
    body = out.read_text(encoding="utf-8")
    assert "{b1}" not in body


# --------------------------------------------------------------------------
# FFmpeg filter-graph construction
# --------------------------------------------------------------------------

@pytest.fixture
def skuld(tmp_path):
    return SkuldRenderer(output_dir=tmp_path)


@pytest.mark.parametrize("crop_mode", [
    "center_crop", "blurred_background", "top_anchored_crop", "cinematic_letterbox",
])
def test_every_crop_mode_targets_the_1080x1920_canvas(skuld, crop_mode):
    graph = skuld._build_crop_filter(crop_mode)
    assert "1080" in graph and "1920" in graph


@pytest.mark.parametrize("crop_mode", ["blurred_background", "top_anchored_crop"])
def test_blurred_modes_crop_back_to_canvas_before_overlay(skuld, crop_mode):
    """
    Regression test. force_original_aspect_ratio=increase deliberately
    OVERSHOOTS the canvas to cover it (a 16:9 source becomes 3413x1920).
    Without a crop back down, libx264 rejects the odd width outright
    ("width not divisible by 2"), and blurring the oversized image was
    also measured ~3x slower.
    """
    graph = skuld._build_crop_filter(crop_mode)
    assert "force_original_aspect_ratio=increase" in graph
    assert "crop=1080:1920" in graph
    # The crop must precede the blur, which is what made it fast.
    assert graph.index("crop=1080:1920") < graph.index("boxblur")


@pytest.mark.parametrize("crop_mode", ["blurred_background", "top_anchored_crop"])
def test_two_layer_modes_split_a_named_input_pad(skuld, crop_mode):
    """
    A bare demuxer pad like [0:v] can be consumed many times, but a
    named filter pad is a one-consumer link — referencing [zoomed] twice
    without split=2 raises "Invalid stream specifier".
    """
    from_named = skuld._build_crop_filter(crop_mode, video_label="[zoomed]")
    assert "split=2" in from_named
    from_raw = skuld._build_crop_filter(crop_mode, video_label="[0:v]")
    assert "split=2" not in from_raw


def test_zoom_effects_use_zoompan_not_time_varying_crop(skuld):
    """
    crop evaluates w/h once at init, so a t-dependent size throws at
    startup; the scale+eval=frame alternative was measured 10-50x slower
    than a normal encode. zoompan is the filter built for this.
    """
    for effect in ("ken_burns_zoom", "punch_in_zoom"):
        stage = skuld._build_zoom_prestage(effect, 8.0, 0.5, 1920, 1080, 30.0)
        assert "zoompan" in stage
        assert stage.endswith("[zoomed];")
        assert "s=1920x1080" in stage, "zoom must run at the source's native size"


def test_zoom_prestage_is_empty_for_non_zoom_effects(skuld):
    for effect in ("none", "shake"):
        assert skuld._build_zoom_prestage(effect, 8.0, 0.5, 1920, 1080, 30.0) == ""


def test_punch_in_zoom_is_more_aggressive_than_ken_burns(skuld):
    def peak_zoom(stage: str) -> float:
        return max(float(m) for m in re.findall(r"1\+([0-9.]+)\*", stage))

    ken = skuld._build_zoom_prestage("ken_burns_zoom", 8.0, 0.5, 1920, 1080, 30.0)
    punch = skuld._build_zoom_prestage("punch_in_zoom", 8.0, 0.5, 1920, 1080, 30.0)
    assert peak_zoom(punch) > peak_zoom(ken)


def test_shake_amplitude_scales_with_crazy(skuld):
    def amplitude(graph: str) -> float:
        return max(float(m) for m in re.findall(r"\+([0-9.]+)\*sin", graph))

    assert amplitude(skuld._build_motion_filter("shake", 1.0)) > \
           amplitude(skuld._build_motion_filter("shake", 0.0))


def test_motion_filter_empty_for_none(skuld):
    assert skuld._build_motion_filter("none", 0.5) == ""


@pytest.mark.parametrize("grade", ["cool_desaturated", "warm_glow", "vibrant_punch"])
def test_colour_grades_emit_a_chainable_eq_filter(skuld, grade):
    graph = skuld._build_color_grade_filter(grade)
    assert graph.startswith(","), "must chain onto the preceding filter"
    assert "eq=" in graph


def test_neutral_grade_is_a_no_op(skuld):
    assert skuld._build_color_grade_filter("neutral") == ""


# --------------------------------------------------------------------------
# Caption typeface selection
# --------------------------------------------------------------------------
# libass substitutes silently for a font it cannot resolve, so a bad name
# does not raise — it changes how the finished video looks with nothing
# logged. That is the failure this guards against.

from agent.skuld_renderer import (  # noqa: E402
    CAPTION_FONTS, CAPTION_FONT, resolve_caption_font,
)


@pytest.mark.parametrize("label,expected", list(CAPTION_FONTS.items()))
def test_each_offered_face_resolves_to_its_family(label, expected):
    assert resolve_caption_font(label) == expected


def test_an_unknown_face_falls_back_to_the_default():
    """Better the configured default than a silent substitution by libass."""
    assert resolve_caption_font("Comic Sans Extreme") == CAPTION_FONT
    assert resolve_caption_font(None) == CAPTION_FONT
    assert resolve_caption_font("") == CAPTION_FONT


def test_the_chosen_face_reaches_the_ass_style_line(tmp_path):
    out = tmp_path / "s.ass"
    generate_rebased_ass_subtitle_file(
        "[00:00.000] One line.\n[00:02.000] Another line.",
        out, 0, 5, caption_font="League Spartan")
    style = next(l for l in out.read_text(encoding="utf-8").splitlines()
                 if l.startswith("Style:"))
    assert style.split(",")[1] == "League Spartan"


def test_omitting_the_face_keeps_the_configured_default(tmp_path):
    out = tmp_path / "s.ass"
    generate_rebased_ass_subtitle_file(
        "[00:00.000] One line.\n[00:02.000] Another.", out, 0, 5)
    style = next(l for l in out.read_text(encoding="utf-8").splitlines()
                 if l.startswith("Style:"))
    assert style.split(",")[1] == CAPTION_FONT


def test_the_dockerfile_installs_every_offered_face():
    """
    A face offered in the UI but absent from the image is the silent-
    substitution bug with extra steps.
    """
    from pathlib import Path
    dockerfile = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text()
    packages = {
        "Roboto Black": "fonts-roboto-unhinted",
        "Roboto Condensed": "fonts-roboto-unhinted",
        "League Spartan": "fonts-league-spartan",
        "Lato Black": "fonts-lato",
        "DejaVu Sans": "fonts-dejavu-core",
    }
    for family in CAPTION_FONTS.values():
        assert packages[family] in dockerfile, f"{family} has no package in the image"


def test_sub_second_timestamps_are_honoured(tmp_path):
    """
    Whole-second timestamps quantise every caption to the nearest second,
    which a reviewer saw as captions being slightly out of sync. The
    transcriber now asks for milliseconds, so the parser must not drop them.
    """
    out = tmp_path / "s.ass"
    generate_rebased_ass_subtitle_file(
        "[00:00.480] First line here.\n[00:02.930] Second line here.", out, 0, 6)
    body = out.read_text(encoding="utf-8")
    # 0.48s must not have been floored to 0.
    first = next(l for l in body.splitlines() if l.startswith("Dialogue:"))
    assert not first.split(",")[1].startswith("0:00:00.00")


# --- the hook banner -------------------------------------------------------
#
# A reviewer rejected a batch of three clips because every title ran off
# both edges of the frame: 1180, 1441 and 1452 pixels of text inside a 1080
# pixel video. The banner had no wrapping at all and a fixed 42px size, so
# any title past roughly forty characters was silently cropped -- and titles
# are written by a model with no notion that a pixel budget exists.

import re as _re

from agent import text_fit as _tf


def _banner(text, warmth=0.5, banner_font=None):
    from agent.skuld_renderer import SkuldRenderer
    return SkuldRenderer.__new__(SkuldRenderer)._build_banner_filter(
        text, warmth, banner_font)


def _parts(f):
    return {
        "font": int(_re.search(r"fontsize=(\d+)", f).group(1)),
        "box_h": int(_re.search(r":h=(\d+)", f).group(1)),
        "text": _re.search(r"text='([^']*)'", f).group(1),
    }


REJECTED_TITLES = [
    "Why NASA Is Landing Telescopes on the Far Side of the Moon",
    "How NASA Plans to Build a Permanent Moon Base",
    "The Extreme Temperature Challenge of the Lunar South Pole",
]


@pytest.mark.parametrize("title", REJECTED_TITLES)
def test_no_banner_line_overflows_the_box(title):
    """The three titles a human actually rejected, pinned."""
    from agent import skuld_renderer as sk

    parts = _parts(_banner(title))
    width_of = _tf.measurer(_tf.font_file(), parts["font"])
    if width_of is None:
        pytest.skip("no measurable font on this machine")
    limit = sk.BANNER_WIDTH - 2 * sk.BANNER_PADDING
    for line in parts["text"].split("\n"):
        assert width_of(line) <= limit, f"{line!r} overflows"


@pytest.mark.parametrize("title", REJECTED_TITLES)
def test_the_box_is_sized_to_the_ink_it_holds(title):
    """
    Sized on the ink, not on the line boxes. A line box is 1.25x the point
    size and the glyphs fill about two thirds of it, so a box built from
    line boxes held 56px of text in 80px of space and the slack collected
    below the words — measured at 25px above and 47px below, which a
    reviewer read as the title not being centred.

    Not asserting that these titles wrap: whether they need two lines
    depends on the face available, and a narrower black weight put all
    three back on one line. The invariant is that the box fits the ink with
    equal padding either side.
    """
    from agent import skuld_renderer as sk
    from agent import text_fit

    f = _banner(title)
    parts = _parts(f)
    lines = [ln for ln in _re.findall(r"text='([^']*)'", f)]
    extents = text_fit.ink_extents(
        lines, text_fit.font_file(), parts["font"], int(parts["font"] * 1.25))
    if extents is None:
        pytest.skip("no measurable font on this machine")
    assert parts["box_h"] == extents[1] + 2 * sk.BANNER_PADDING


def test_a_two_line_banner_is_taller_than_a_one_line_one():
    """Whatever the sizing rule, more lines must mean more box."""
    one = _parts(_banner("Short"))["box_h"]
    two = _parts(_banner("A considerably longer title that will certainly "
                         "need to wrap onto a second line"))["box_h"]
    assert two > one


@pytest.mark.parametrize("title", REJECTED_TITLES)
def test_every_line_is_centred_on_the_box(title):
    """
    One drawtext per line, each centred on its own width. A single drawtext
    with embedded newlines left-aligns them inside the block, which is what
    a reviewer saw as "the title is misaligned".
    """
    from agent import skuld_renderer as sk

    f = _banner(title)
    centre = sk.BANNER_X + sk.BANNER_WIDTH / 2
    assert f.count("drawtext=") >= 1
    assert f"x={centre:.0f}-text_w/2" in f
    assert "\n" not in _parts(f)["text"]


def test_the_banner_names_a_concrete_font_file():
    """
    drawtext cannot resolve family names, and with no fontfile it silently
    falls back to a regular weight that disappears over video. The banner
    shipped that way.
    """
    from agent import text_fit
    if not text_fit.font_file():
        pytest.skip("no usable font on this machine")
    assert "fontfile=" in _banner("Any title at all")


def test_a_short_title_stays_on_one_line():
    parts = _parts(_banner("Short title"))
    assert "\n" not in parts["text"]


def test_empty_text_produces_no_banner():
    assert _banner("") == ""


def test_the_banner_still_draws_a_box_and_text():
    f = _banner("A perfectly ordinary title")
    assert "drawbox=" in f and "drawtext=" in f


# --- choosing a face --------------------------------------------------------
#
# drawtext cannot resolve family names and needs a path; libass can resolve
# them and substitutes silently when it fails, which is worse. So a face has
# to exist as a file in both the workstation and the container, or a render
# looks one way locally and another way deployed. Arial Black was rejected
# as the default for exactly that reason.

def test_a_named_face_reaches_the_filter():
    from agent import text_fit
    if "Bebas Neue" not in text_fit.available_faces():
        pytest.skip("bundled fonts not fetched")
    assert "BebasNeue" in _banner("A title", 0.5, "Bebas Neue")


def test_an_unknown_face_falls_back_rather_than_failing():
    """A typo in a channel profile should cost the choice, not the render."""
    f = _banner("A title", 0.5, "Definitely Not A Font")
    assert "drawtext=" in f and "fontfile=" in f


def test_the_bundled_faces_are_preferred_over_system_ones():
    from agent import text_fit
    if not text_fit.available_faces():
        pytest.skip("bundled fonts not fetched")
    assert "assets/fonts" in (text_fit.font_file() or "")


def test_every_advertised_face_is_actually_present():
    """
    A face offered but not on disk renders as something else, silently.
    scripts/fetch_fonts.py is what keeps these in step.
    """
    from agent import text_fit
    missing = set(text_fit.DISPLAY_FACES) - set(text_fit.available_faces())
    if missing == set(text_fit.DISPLAY_FACES):
        pytest.skip("bundled fonts not fetched")
    assert not missing, f"advertised but absent: {sorted(missing)}"
