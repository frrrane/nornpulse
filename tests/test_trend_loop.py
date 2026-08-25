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


# --- voice grounding -------------------------------------------------------

def test_voice_reference_reaches_the_prompt(monkeypatch):
    """
    Asking a model to "be funny" returns the median of everything it has
    read, which is polished corporate deadpan. This channel's actual
    register is chaotic mashups, and it has already demonstrated that in
    titles with real view counts attached.
    """
    seen = {}

    class _Resp:
        text = json.dumps(BRIEF)

    class _Models:
        def generate_content(self, **kw):
            seen["prompt"] = kw.get("contents", "")
            return _Resp()

    class _Client:
        def __init__(self, **kw): self.models = _Models()

    import google.genai as genai
    monkeypatch.setattr(genai, "Client", _Client)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")

    tl.write_brief(_Channel(), tl.candidate_topics(TRENDING),
                   voice=[{"title": "The Sopurranos but it's Cats", "views": 1299}])
    assert "Sopurranos" in seen["prompt"]
    assert "1,299" in seen["prompt"]


def test_prompt_warns_against_the_deadpan_default(monkeypatch):
    """A regression guard: this direction was added because output kept
    converging on boardrooms and unbothered pitchmen."""
    assert "CORPORATE DEADPAN" in tl._BRIEF_PROMPT
    assert "boardroom" in tl._BRIEF_PROMPT


def test_ip_constraint_survives_the_voice_reference():
    """
    The channel's best performers lean heavily on copyrighted characters and
    real people. Matching its energy must not mean copying that.
    """
    assert "no copyrighted characters" in tl._BRIEF_PROMPT
    assert "no real identifiable people" in tl._BRIEF_PROMPT


# --- the look, not just the joke -------------------------------------------
#
# The first two comedy generations were executed faithfully and were not
# funny. Both came back cinematic: golden hour, shallow depth of field,
# beautifully graded. The premise survived review; the polish killed it. So
# the aesthetic direction is now part of the brief, and the terms that would
# sand the texture off are removed rather than merely discouraged.

class _ScienceChannel:
    slug = "nornpulse"
    title = "Norn Labs"

    class profile:
        category_id = "28"
        topic_hints = ["science", "space", "technology"]
        music_mood = "dramatic"


def _recording_stub(monkeypatch, payload):
    """Like _stub_model, but keeps the prompt that was sent."""
    seen = {}
    text = payload if isinstance(payload, str) else json.dumps(payload)

    class _Resp:
        def __init__(self): self.text = text

    class _Models:
        def generate_content(self, **kw):
            seen["prompt"] = kw.get("contents", "")
            return _Resp()

    class _Client:
        def __init__(self, **kw): self.models = _Models()

    import google.genai as genai
    monkeypatch.setattr(genai, "Client", _Client)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    return seen


def test_comedy_channel_is_recognised_by_category_or_hints():
    assert tl.is_comedy(_Channel)
    assert not tl.is_comedy(_ScienceChannel)


@pytest.mark.parametrize("term", [
    "low quality", "blurry", "glitchy artifacts", "deformed hands",
    "ugly", "uncanny", "grainy", "pixelated", "bad anatomy",
])
def test_polish_guards_are_stripped(term):
    """Each of these describes the aesthetic, not the failure."""
    out = tl.strip_polish_guards(f"watermarks, {term}, text overlay")
    assert term not in out
    assert "watermarks" in out and "text overlay" in out


def test_stripping_only_removes_whole_items():
    """"a glitch in the mainframe" is a subject, not a quality complaint."""
    kept = tl.strip_polish_guards("a glitch in the mainframe, blurry")
    assert kept == "a glitch in the mainframe"


def test_stripping_survives_an_empty_or_total_match():
    assert tl.strip_polish_guards("") == ""
    assert tl.strip_polish_guards("blurry, low quality") == ""


def test_comedy_brief_keeps_its_texture(monkeypatch):
    payload = dict(BRIEF)
    payload["negative_prompt"] = "logos, low quality, glitchy artifacts, watermarks"
    _stub_model(monkeypatch, payload)
    brief = tl.write_brief(_Channel, tl.candidate_topics(TRENDING), voice=[])
    assert "low quality" not in brief.negative_prompt
    assert "glitchy artifacts" not in brief.negative_prompt
    assert "logos" in brief.negative_prompt and "watermarks" in brief.negative_prompt


def test_a_science_channel_keeps_its_polish_guards(monkeypatch):
    """
    Stripping is for channels whose humour depends on things looking wrong.
    A science channel asking not to be blurry means it.
    """
    payload = dict(BRIEF)
    payload["negative_prompt"] = "low quality, blurry"
    _stub_model(monkeypatch, payload)
    brief = tl.write_brief(_ScienceChannel, tl.candidate_topics(TRENDING), voice=[])
    assert brief.negative_prompt == "low quality, blurry"


def test_the_look_direction_reaches_a_comedy_brief_only(monkeypatch):
    seen = _recording_stub(monkeypatch, BRIEF)
    tl.write_brief(_Channel, tl.candidate_topics(TRENDING), voice=[])
    assert "HOW IT MUST LOOK" in seen["prompt"]
    assert "shallow depth of field" in seen["prompt"]

    seen = _recording_stub(monkeypatch, BRIEF)
    tl.write_brief(_ScienceChannel, tl.candidate_topics(TRENDING), voice=[])
    assert "HOW IT MUST LOOK" not in seen["prompt"]


# --- three premises, then a choice -----------------------------------------

def _candidates(*angles, pick=0):
    return {
        "suitable": True,
        "candidates": [dict(BRIEF, angle=a, title=f"title {i}")
                       for i, a in enumerate(angles)],
        "pick": pick,
        "pick_reason": "the stupidest one",
    }


def test_the_model_choice_is_honoured(monkeypatch):
    _stub_model(monkeypatch, _candidates("first", "second", "third", pick=2))
    brief = tl.write_brief(_Channel, tl.candidate_topics(TRENDING), voice=[])
    assert brief.angle == "third"
    assert brief.extra["pick_reason"] == "the stupidest one"


def test_the_rejected_premises_are_kept_for_review(monkeypatch):
    """
    A reviewer seeing only the winner cannot tell whether the choice was
    good. The two it beat are cheap to carry and make the decision legible.
    """
    _stub_model(monkeypatch, _candidates("first", "second", "third", pick=1))
    brief = tl.write_brief(_Channel, tl.candidate_topics(TRENDING), voice=[])
    alternatives = brief.extra["alternatives"]
    assert len(alternatives) == 2
    assert {a["angle"] for a in alternatives} == {"first", "third"}


@pytest.mark.parametrize("bad", [9, -1, "two", None])
def test_an_unusable_pick_falls_back_rather_than_failing(bad, monkeypatch):
    """Losing the choice is a shame; losing three written premises is worse."""
    _stub_model(monkeypatch, _candidates("first", "second", pick=bad))
    brief = tl.write_brief(_Channel, tl.candidate_topics(TRENDING), voice=[])
    assert brief is not None
    assert brief.angle in ("first", "second")


def test_a_candidate_with_an_invented_topic_is_dropped_not_fatal(monkeypatch):
    """
    One bad candidate must not cost the other two — but it must not be
    published with measured trend numbers attached to a tag nobody measured.
    """
    payload = _candidates("first", "second", pick=0)
    payload["candidates"][0]["topic"] = "a tag that was never trending"
    _stub_model(monkeypatch, payload)
    brief = tl.write_brief(_Channel, tl.candidate_topics(TRENDING), voice=[])
    assert brief.angle == "second"
    assert brief.topic == "funny moments"


def test_a_single_object_response_still_works(monkeypatch):
    """Older shape, and what a model returns when it ignores the list."""
    _stub_model(monkeypatch, BRIEF)
    brief = tl.write_brief(_Channel, tl.candidate_topics(TRENDING), voice=[])
    assert brief is not None
    assert brief.extra["alternatives"] == []


# --- catching a bad brief before it is paid for ----------------------------
#
# Both of these were found by a human watching the finished video, which is
# the expensive way: the clip was generated and billed first. Both are
# plainly visible in the text of the brief.

def _b(prompt="", title=""):
    return tl.Brief(topic="florida", angle="", video_prompt=prompt,
                    title=title, caption="")


@pytest.mark.parametrize("ending", [
    "the alligator sits upright in it and stares blankly past the lens.",
    "he holds the pose.",
    "the knight remains motionless.",
    "she looks at the camera.",
    "the gator stands still.",
])
def test_a_prompt_ending_on_a_held_pose_is_flagged(ending):
    """
    Veo fills unwritten time by holding the frame, so a brief that stops
    describing action at five seconds spends three of eight seconds static.
    """
    w = tl.brief_warnings(_b(prompt="Things happen. " + ending))
    assert any("held pose" in x for x in w)


def test_a_prompt_ending_in_motion_is_not_flagged():
    w = tl.brief_warnings(_b(
        prompt="A setup. Then a turn. Finally the chair collapses and he "
               "scrambles backwards through the mud."))
    assert not any("held pose" in x for x in w)


def test_only_the_final_sentence_decides():
    """Sitting down mid-clip is fine; ending there is not."""
    w = tl.brief_warnings(_b(
        prompt="The gator sits in a chair. Then it hurls the chair into a pond."))
    assert not any("held pose" in x for x in w)


@pytest.mark.parametrize("title", [
    "Florida Lawn Care Accordion Spell Backfires!",
    "Gator Lawn Care Ends in Slushie Crisis",
    "You Won't Believe What This Gator Did",
    "Swamp Wedding Goes Wrong",
])
def test_a_title_promising_an_unshown_outcome_is_flagged(title):
    w = tl.brief_warnings(_b(title=title))
    assert any("promises an outcome" in x for x in w)


def test_a_descriptive_title_is_not_flagged():
    w = tl.brief_warnings(_b(title="Alligator In A Lawn Chair Drinks A Slushie"))
    assert not any("promises an outcome" in x for x in w)


def test_the_exact_rejected_brief_is_caught_on_both_counts():
    """The clip a human rejected, pinned so the check cannot regress."""
    w = tl.brief_warnings(_b(
        title="Florida Lawn Care Accordion Spell Backfires!",
        prompt=("A sweaty man plays a red accordion at a giant alligator. "
                "At second 4 the alligator unfolds a white plastic lawn "
                "chair, sits upright in it, and stares blankly past the lens.")))
    assert len(w) == 2


def test_an_empty_brief_produces_no_warnings():
    assert tl.brief_warnings(_b()) == []
