"""
Unit tests for Verðandi's pure orchestration logic.

The focus is the duration/window clamp, which is the safety net that
makes a user's Cut Range a hard guarantee rather than a prompt-level
suggestion the model might ignore. Everything here runs without a Gemini
client, a ClickHouse connection, or FFmpeg: _make_tools is built against
a stub Urðr/Skuld so the closure under test can be exercised directly.
"""

import types

import pytest

from agent.verdandi_orchestrator import (
    VerdandiADK,
    _clean_transcript_window_text,
    filter_transcript_by_window,
)
from agent.skuld_renderer import parse_time_to_seconds


# --------------------------------------------------------------------------
# Transcript windowing
# --------------------------------------------------------------------------

TRANSCRIPT = """[00:05] way before
[00:30] inside the window
[00:45] also inside
[02:00] long after
"""


def test_filter_transcript_by_window_keeps_only_lines_inside():
    kept = filter_transcript_by_window(TRANSCRIPT, (20.0, 60.0))
    assert "inside the window" in kept
    assert "also inside" in kept
    assert "way before" not in kept
    assert "long after" not in kept


def test_filter_transcript_by_window_is_a_noop_without_a_window():
    assert filter_transcript_by_window(TRANSCRIPT, None) == TRANSCRIPT


def test_filter_transcript_by_window_boundaries_are_inclusive():
    assert "way before" in filter_transcript_by_window(TRANSCRIPT, (5.0, 10.0))


def test_filter_transcript_by_window_can_return_empty():
    """A window over a silent stretch yields nothing, which is what makes
    the caller correctly fall back to vision mode for that stretch."""
    assert filter_transcript_by_window(TRANSCRIPT, (70.0, 90.0)).strip() == ""


def test_filter_transcript_handles_empty_input():
    assert filter_transcript_by_window("", (0.0, 10.0)) == ""


def test_clean_transcript_window_text_strips_timestamps():
    cleaned = _clean_transcript_window_text("[00:30] hello there\n[00:32] second bit")
    assert "[" not in cleaned and "00:30" not in cleaned
    assert cleaned == "hello there second bit"


def test_clean_transcript_window_text_on_empty():
    assert _clean_transcript_window_text("") == ""


# --------------------------------------------------------------------------
# Duration / window clamping
# --------------------------------------------------------------------------

class _StubUrdr:
    def get_top_music_benchmark(self, **kw):
        return None

    def get_top_visual_benchmark(self, **kw):
        return None


def _clamp_fn(window=None, min_d=8.0, max_d=15.0, video_len=600.0):
    """
    Builds the real tool closures against stub collaborators and digs out
    the _clamp_duration closure, so the genuine production logic is under
    test rather than a reimplementation of it.
    """
    adk = VerdandiADK.__new__(VerdandiADK)   # bypass __init__ (needs an API key)
    adk.urdr = _StubUrdr()
    adk.skuld = types.SimpleNamespace(output_dir="/tmp")

    tools = VerdandiADK._make_tools(
        adk,
        transcript_text="",
        rendered_clips=[],
        warmth=0.5,
        crazy=0.3,
        retention_summary={"hook_taxonomies": []},
        min_duration_sec=min_d,
        max_duration_sec=max_d,
        video_duration_sec=video_len,
        window=window,
    )
    render_tool = tools[0]
    return render_tool.__wrapped__ if hasattr(render_tool, "__wrapped__") else render_tool


def _get_clamp():
    """Extract _clamp_duration from the render tool's enclosing scope."""
    adk = VerdandiADK.__new__(VerdandiADK)
    adk.urdr = _StubUrdr()
    adk.skuld = types.SimpleNamespace(output_dir="/tmp")

    def build(window, min_d=8.0, max_d=15.0, video_len=600.0):
        tools = VerdandiADK._make_tools(
            adk, transcript_text="", rendered_clips=[], warmth=0.5, crazy=0.3,
            retention_summary={"hook_taxonomies": []},
            min_duration_sec=min_d, max_duration_sec=max_d,
            video_duration_sec=video_len, window=window,
        )
        fn = tools[0]
        cells = dict(zip(fn.__code__.co_freevars, fn.__closure__ or ()))
        return cells["_clamp_duration"].cell_contents
    return build


@pytest.fixture
def clamp_builder():
    return _get_clamp()


def test_clamp_enforces_maximum_duration(clamp_builder):
    clamp = clamp_builder(None)
    start, end = clamp("00:10", "01:00")   # 50s requested, max is 15
    assert parse_time_to_seconds(end) - parse_time_to_seconds(start) == pytest.approx(15.0)


def test_clamp_enforces_minimum_duration(clamp_builder):
    clamp = clamp_builder(None)
    start, end = clamp("00:10", "00:12")   # 2s requested, min is 8
    assert parse_time_to_seconds(end) - parse_time_to_seconds(start) == pytest.approx(8.0)


def test_clamp_leaves_an_in_range_request_alone(clamp_builder):
    clamp = clamp_builder(None)
    assert clamp("00:10", "00:22") == ("00:10", "00:22")


def test_clamp_confines_a_clip_to_the_user_window(clamp_builder):
    """
    The whole point of the code-level clamp: even if the model ignores
    the prompt and asks for a range outside the user's Cut Range, it
    must not be able to render outside it.
    """
    clamp = clamp_builder((60.0, 120.0))
    start, end = clamp("00:10", "00:25")   # entirely before the window
    assert parse_time_to_seconds(start) >= 60.0
    assert parse_time_to_seconds(end) <= 120.0


def test_clamp_truncates_a_clip_overrunning_the_window_end(clamp_builder):
    clamp = clamp_builder((60.0, 75.0))
    start, end = clamp("01:10", "01:30")   # 70s -> 90s, window ends at 75s
    assert parse_time_to_seconds(end) <= 75.0


def test_clamp_never_exceeds_the_source_video_length(clamp_builder):
    clamp = clamp_builder(None, video_len=30.0)
    _, end = clamp("00:20", "00:40")
    # A safety buffer keeps it off the exact final frame.
    assert parse_time_to_seconds(end) <= 29.0


@pytest.mark.parametrize("requested", [
    ("00:00", "00:01"),   # entirely before the window
    ("00:00", "10:00"),   # spans far past both ends
    ("05:00", "05:02"),   # entirely AFTER the window — the crossing case
    ("09:50", "10:30"),   # far after, and past the source length
    ("02:00", "02:00"),   # zero-length request
    ("03:30", "02:30"),   # inverted request (end before start)
])
def test_clamp_always_respects_the_window_whatever_is_requested(clamp_builder, requested):
    """
    Property test over adversarial ranges. Regression guard: a request
    landing entirely outside the window used to clamp start UP and end
    DOWN independently, crossing them over and producing end < start —
    an invalid FFmpeg range. The clamp is the hard guarantee behind the
    user's Cut Range, so it has to hold for anything the model emits.
    """
    window = (120.0, 180.0)
    clamp = clamp_builder(window)
    start, end = clamp(*requested)
    s, e = parse_time_to_seconds(start), parse_time_to_seconds(end)
    assert s >= window[0] - 1e-6, f"{requested} -> start {s} escaped window {window}"
    assert e <= window[1] + 1e-6, f"{requested} -> end {e} escaped window {window}"
    assert e > s, f"{requested} -> invalid range {s}..{e} (end not after start)"


@pytest.mark.parametrize("window", [(0.0, 20.0), (120.0, 180.0), (500.0, 599.0)])
@pytest.mark.parametrize("requested", [
    ("00:00", "00:05"), ("04:00", "04:30"), ("09:00", "09:10"), ("00:30", "08:00"),
])
def test_clamp_produces_a_valid_range_across_window_positions(clamp_builder, window, requested):
    clamp = clamp_builder(window)
    s, e = (parse_time_to_seconds(v) for v in clamp(*requested))
    assert e > s
    assert s >= window[0] - 1e-6
    assert e <= window[1] + 1e-6


# --------------------------------------------------------------------------
# Metadata reconciliation
# --------------------------------------------------------------------------

def _reconcile(rendered, parsed, prefix=""):
    adk = VerdandiADK.__new__(VerdandiADK)
    return VerdandiADK._reconcile_metadata(adk, parsed, rendered, clip_id_prefix=prefix)


_RENDERED = [{
    "clip_id": "batch0_clip_001", "start_time": "00:10", "end_time": "00:22",
    "output_video_path": "/out/batch0_clip_001_9x16.mp4",
    "has_subtitles": True, "has_bragi_score": True, "has_narration": False,
    "caption_language": "English",
    "thumbnail_path": "/out/thumb.png",
    "music_genre": "synthwave", "music_mood": "mysterious",
    "crop_mode": "cinematic_letterbox", "motion_effect": "shake",
    "color_grade": "cool_desaturated",
    "hook_type": "contrarian_claim", "hook_rank": 1, "is_top_tier_hook": True,
    "grounded_top_hook_type": "contrarian_claim",
}]


def test_reconcile_carries_every_render_field_through():
    """
    Regression test. _reconcile_metadata used to rebuild the output dict
    field by field, silently dropping anything not explicitly listed —
    crop_mode, motion_effect, color_grade and caption_language all
    arrived as None, so the UI's "translated to X" badge could never
    render even for a genuinely translated clip. Found by a real batch run.
    """
    out, = _reconcile(_RENDERED, [{"clip_id": "clip_001", "hook_title": "T",
                                   "social_caption": "C", "virality_score": 91.0}],
                      prefix="batch0_")
    for field, expected in [
        ("caption_language", "English"),
        ("crop_mode", "cinematic_letterbox"),
        ("motion_effect", "shake"),
        ("color_grade", "cool_desaturated"),
        ("music_genre", "synthwave"),
        ("thumbnail_path", "/out/thumb.png"),
    ]:
        assert out[field] == expected, f"{field} was dropped by reconciliation"


def test_reconcile_applies_the_prefix_when_matching_model_metadata():
    """Gemini emits unprefixed clip_ids; render records are namespaced."""
    out, = _reconcile(_RENDERED, [{"clip_id": "clip_001", "hook_title": "Real Title",
                                   "social_caption": "c", "virality_score": 88.0}],
                      prefix="batch0_")
    assert out["hook_title"] == "Real Title"
    assert out["virality_score"] == 88.0


def test_reconcile_falls_back_when_model_metadata_is_missing():
    """A malformed closing JSON must never orphan a clip that really rendered."""
    out, = _reconcile(_RENDERED, [])
    assert out["output_video_path"] == "/out/batch0_clip_001_9x16.mp4"
    assert out["hook_title"]           # a default, not a crash
    assert out["crop_mode"] == "cinematic_letterbox"


def test_reconcile_prefers_render_record_over_model_for_factual_fields():
    out, = _reconcile(_RENDERED, [{"clip_id": "clip_001", "hook_type": "WRONG"}],
                      prefix="batch0_")
    assert out["hook_type"] == "contrarian_claim"


def test_reconcile_returns_empty_when_nothing_rendered():
    assert _reconcile([], [{"clip_id": "clip_1", "hook_title": "x"}]) == []
