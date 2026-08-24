"""
Unit tests for trend-driven generation.

The model is stubbed; no Gemini call is made and nothing is generated. What
matters here is what the loop refuses to do, because each refusal protects
something expensive: a fabricated measurement, an off-brand video nobody
asked for, or a copyright claim against the channel.
"""

import json

import pandas as pd
import pytest

from agent import provenance as pv
from agent import trend_loop as tl


TRENDING = pd.DataFrame([
    {"tag": "minecraft", "videos": 9, "median_views": 150255},
    {"tag": "funny moments", "videos": 3, "median_views": 279216},
    {"tag": "gaming", "videos": 3, "median_views": 222869},
    {"tag": "one-off", "videos": 1, "median_views": 900000},
])


class _Channel:
    slug = "sloptokdaily"
    title = "SlopTokDaily"

    class profile:
        topic_hints = ["funny", "comedy", "ai"]
        music_mood = "playful"


def _stub_model(monkeypatch, payload):
    """Replace genai.Client so write_brief gets a fixed response."""
    text = payload if isinstance(payload, str) else json.dumps(payload)

    class _Resp:
        def __init__(self): self.text = text

    class _Models:
        def generate_content(self, **kw): return _Resp()

    class _Client:
        def __init__(self, **kw): self.models = _Models()

    import google.genai as genai
    monkeypatch.setattr(genai, "Client", _Client)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")


BRIEF = {
    "suitable": True,
    "topic": "funny moments",
    "angle": "A capybara judge presides over an absurd courtroom.",
    "video_prompt": "Vertical 9:16. A capybara in a powdered wig strikes a gavel.",
    "negative_prompt": "logos, watermarks",
    "title": "The most honorable judge in history",
    "caption": "Order in the court.",
    "hook_type": "visual_disruption",
    "rationale": "AI comedy suits this channel.",
}


# --- topic shortlisting ----------------------------------------------------

def test_single_video_tags_are_not_treated_as_trends():
    """
    One video carrying a tag is noise. Labelling it MEASURED would put the
    authority of a real count behind something close to nothing — and the
    900k median on that row makes it the most tempting one to keep.
    """
    tags = [t["tag"] for t in tl.candidate_topics(TRENDING)]
    assert "one-off" not in tags


def test_topics_are_ranked_by_how_many_videos_carry_them():
    tags = [t["tag"] for t in tl.candidate_topics(TRENDING)]
    assert tags[0] == "minecraft"


def test_empty_or_missing_snapshot_yields_no_candidates():
    assert tl.candidate_topics(pd.DataFrame()) == []
    assert tl.candidate_topics(None) == []


# --- the refusals ----------------------------------------------------------

def test_model_may_decline_every_topic(monkeypatch):
    """
    A comedy channel is not obliged to have a take on whatever is popular.
    Forcing one produces the off-brand filler this loop exists to avoid.
    """
    _stub_model(monkeypatch, {"suitable": False, "why": "all gaming, not our thing"})
    assert tl.write_brief(_Channel(), tl.candidate_topics(TRENDING)) is None


def test_invented_topic_is_rejected(monkeypatch):
    """
    The measured trend numbers are attached to the topic. If the model
    returns a tag that was never in the snapshot, those numbers would
    describe something that was never measured.
    """
    payload = dict(BRIEF, topic="something the model made up")
    _stub_model(monkeypatch, payload)
    assert tl.write_brief(_Channel(), tl.candidate_topics(TRENDING)) is None


def test_unparseable_response_returns_none(monkeypatch):
    _stub_model(monkeypatch, "I'm afraid I can't help with that.")
    assert tl.write_brief(_Channel(), tl.candidate_topics(TRENDING)) is None


def test_no_topics_means_no_brief(monkeypatch):
    _stub_model(monkeypatch, BRIEF)
    assert tl.write_brief(_Channel(), []) is None


# --- the brief -------------------------------------------------------------

def test_brief_carries_the_measured_trend_numbers(monkeypatch):
    _stub_model(monkeypatch, BRIEF)
    b = tl.write_brief(_Channel(), tl.candidate_topics(TRENDING))
    assert b.topic == "funny moments"
    assert b.trend_videos == 3
    assert b.trend_median_views == 279216


def test_topic_is_measured_but_the_angle_is_not(monkeypatch):
    """
    Collapsing these would let a confident guess inherit the authority of a
    real count. The trend is counted; the take on it is invention.
    """
    _stub_model(monkeypatch, BRIEF)
    b = tl.write_brief(_Channel(), tl.candidate_topics(TRENDING))
    by_step = {d.step: d for d in b.decisions()}
    assert by_step["Topic"].level == pv.MEASURED
    assert by_step["Topic"].sample == 3
    assert by_step["Angle"].level == pv.MODEL
    assert by_step["Angle"].sample is None


def test_topic_match_is_case_insensitive(monkeypatch):
    _stub_model(monkeypatch, dict(BRIEF, topic="FUNNY MOMENTS"))
    b = tl.write_brief(_Channel(), tl.candidate_topics(TRENDING))
    assert b is not None and b.topic == "funny moments"


def test_brief_converts_to_the_clip_shape_publishing_expects(monkeypatch):
    _stub_model(monkeypatch, BRIEF)
    b = tl.write_brief(_Channel(), tl.candidate_topics(TRENDING))
    clip = b.as_clip("trend_x")
    assert clip["clip_id"] == "trend_x"
    assert clip["hook_title"] == BRIEF["title"]
    assert clip["social_caption"] == BRIEF["caption"]
    assert clip["hook_type"] == "visual_disruption"


def test_overlong_title_is_truncated(monkeypatch):
    _stub_model(monkeypatch, dict(BRIEF, title="x" * 400))
    b = tl.write_brief(_Channel(), tl.candidate_topics(TRENDING))
    assert len(b.title) <= 100


# --- response parsing ------------------------------------------------------

@pytest.mark.parametrize("wrapped", [
    '{"a": 1}',
    '```json\n{"a": 1}\n```',
    '```\n{"a": 1}\n```',
    'Here you go:\n{"a": 1}\nhope that helps',
])
def test_json_is_recovered_from_the_shapes_models_actually_return(wrapped):
    assert tl._json_from(wrapped) == {"a": 1}


def test_json_parsing_gives_up_cleanly():
    assert tl._json_from("no json here") is None
    assert tl._json_from("") is None
