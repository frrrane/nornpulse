"""
Unit tests for decision provenance.

The point of this module is that a hook ranked against 9,100 real videos
and a colour grade read out of a sixteen-row seeded table are different
kinds of claim, and the UI must not present them identically. So the
tests that matter are about *level* — whether something is reported as
measured when it is only assumed. Getting that wrong overstates the
system's grounding, which is the specific dishonesty this module exists
to prevent.
"""

import pandas as pd
import pytest

from agent import provenance as pv


CLIP = {
    "clip_id": "clip_1", "start_time": "00:22", "end_time": "00:34",
    "hook_type": "curiosity_gap", "has_subtitles": True,
    "caption_language": None, "crop_mode": "blurred_background",
    "motion_effect": "ken_burns_zoom", "color_grade": "cool_desaturated",
    "has_bragi_score": True, "music_genre": "synthwave", "music_mood": "mysterious",
}

FACTS = pd.DataFrame(
    [{"dimension": "hook_pattern", "bucket": b, "size_band": "0-100",
      "median_views": mv, "sample_videos": n}
     for b, mv, n in [("curiosity_gap", 3965.0, 9100), ("plain", 3457.0, 160751)]]
    + [{"dimension": "has_subtitles", "bucket": b, "size_band": "0-100",
        "median_views": mv, "like_rate_pct": lr, "sample_videos": n}
       for b, mv, lr, n in [("true", 2421.0, 1.208, 29044), ("false", 2556.0, 0.721, 29000)]]
    + [{"dimension": "channel_size_band", "bucket": "0-100", "size_band": "",
        "median_views": 2492.0, "p10_views": 1170.0, "p90_views": 15680.0,
        "sample_videos": 58044}]
)


def _by_step(clip=CLIP, facts=FACTS, subs=0):
    return {d.step: d for d in pv.decisions_for_clip(clip, subs, facts)}


# --------------------------------------------------------------------------
# Levels
# --------------------------------------------------------------------------

def test_the_hook_is_measured_and_carries_its_sample():
    hook = _by_step()["Hook"]
    assert hook.level == pv.MEASURED
    assert hook.sample == 9100
    assert "+15%" in hook.evidence


def test_visual_treatment_is_never_reported_as_measured():
    """
    The public dataset has no crop mode, camera motion or colour grade, so
    there is nothing to measure these against. Reporting them as measured
    would be the single most misleading thing this panel could do.
    """
    steps = _by_step()
    for step in ("Framing", "Camera motion", "Colour grade"):
        assert steps[step].level == pv.PRIOR, step
        assert steps[step].sample is None
        assert "not measured" in steps[step].evidence.lower() or \
               "no visual features" in steps[step].evidence.lower()


def test_music_is_never_reported_as_measured():
    score = _by_step()["Score"]
    assert score.level == pv.PRIOR
    assert "no audio features" in score.evidence.lower()


def test_the_cut_is_model_judgement_not_evidence():
    cut = _by_step()["Cut"]
    assert cut.level == pv.MODEL
    assert cut.sample is None


def test_captions_report_the_banded_effect():
    caps = _by_step()["Captions"]
    assert caps.level == pv.MEASURED
    # In the 0-100 band captions lift engagement but not reach, and the
    # evidence line must say so rather than implying a reach gain.
    assert "no measurable reach lift" in caps.evidence
    import re
    like = re.search(r"\+(\d+)% like rate", caps.evidence)
    assert like and int(like.group(1)) > 50


# --------------------------------------------------------------------------
# Degradation
# --------------------------------------------------------------------------

def test_a_hook_with_no_global_figure_is_marked_prior():
    """visual_disruption cannot be identified from a title, so it has none."""
    clip = {**CLIP, "hook_type": "visual_disruption"}
    hook = _by_step(clip)["Hook"]
    assert hook.level == pv.PRIOR
    assert "cannot be identified from a video title" in hook.evidence


def test_without_global_facts_nothing_claims_to_be_measured():
    """No materialised data must never silently become confident evidence."""
    decisions = pv.decisions_for_clip(CLIP, 0, pd.DataFrame())
    assert all(d.level != pv.MEASURED for d in decisions)


def test_absent_fields_produce_no_decision():
    bare = {"clip_id": "x", "hook_type": "curiosity_gap"}
    steps = _by_step(bare)
    assert "Framing" not in steps and "Score" not in steps and "Cut" not in steps
    assert "Hook" in steps


def test_a_clip_without_subtitles_has_no_caption_decision():
    assert "Captions" not in _by_step({**CLIP, "has_subtitles": False})


# --------------------------------------------------------------------------
# Banding
# --------------------------------------------------------------------------

def test_evidence_is_read_within_the_channel_size_band():
    """A figure quoted for the wrong band is worse than no figure."""
    assert "0-100-subscriber" in _by_step(subs=0)["Hook"].evidence
    # A band with no materialised hook data must degrade, not borrow.
    assert _by_step(subs=5_000_000)["Hook"].level == pv.PRIOR


def test_summary_counts_every_decision():
    decisions = pv.decisions_for_clip(CLIP, 0, FACTS)
    counts = pv.grounding_summary(decisions)
    assert sum(counts.values()) == len(decisions)
    assert counts[pv.PRIOR] >= 4      # framing, motion, colour, score
    assert counts[pv.MEASURED] >= 2   # hook, captions


@pytest.mark.parametrize("level", [pv.MEASURED, pv.PRIOR, pv.MODEL])
def test_every_level_has_a_human_label(level):
    assert pv.LEVEL_LABEL[level]
