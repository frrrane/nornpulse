"""
Unit tests for grounded tag selection.

Nothing here touches ClickHouse: the trending snapshot is passed in as a
DataFrame, which is how select_tags takes it anyway. The properties worth
guarding are the ones whose failure is silent and expensive — a tag the
clip cannot justify (keyword stuffing, which risks the channel rather than
the clip), a tag list that overruns YouTube's 500-character cap and fails
the whole upload, and a mis-ranked tag caused by case-variant collisions.
"""

import pandas as pd
import pytest

from agent import provenance as pv
from agent import tag_selector as ts


TRENDING = pd.DataFrame([
    {"tag": "minecraft", "videos": 9, "median_views": 150255},
    {"tag": "funny", "videos": 6, "median_views": 439458},
    {"tag": "gaming", "videos": 4, "median_views": 222869},
    {"tag": "drake", "videos": 3, "median_views": 156759},
])

COMEDY_CLIP = {
    "clip_id": "c1",
    "hook_title": "Funny Minecraft Gaming Fails",
    "social_caption": "gaming clips that went wrong",
    "topic_category": "comedy",
}

SCIENCE_CLIP = {
    "clip_id": "c2",
    "hook_title": "Are We Living Inside a White Hole?",
    "social_caption": "Our expanding universe, explained.",
}


def _levels(decisions):
    return {d.choice: d.level for d in decisions}


# --- the stuffing guard ----------------------------------------------------

def test_unrelated_trending_tags_are_never_added():
    """
    The whole point of the design: trending data ranks candidates, it does
    not supply them. A science clip must not pick up "minecraft" merely
    because minecraft is trending — that is keyword stuffing, and YouTube
    penalises the channel for it.
    """
    tags, _ = ts.select_tags(SCIENCE_CLIP, trending=TRENDING)
    for trending_tag in ("minecraft", "funny", "gaming", "drake"):
        assert trending_tag not in tags


def test_relevant_trending_tag_is_measured_with_its_sample():
    tags, decisions = ts.select_tags(COMEDY_CLIP, trending=TRENDING)
    levels = _levels(decisions)
    assert levels["minecraft"] == pv.MEASURED
    minecraft = next(d for d in decisions if d.choice == "minecraft")
    assert minecraft.sample == 9
    assert "9" in minecraft.evidence


def test_relevant_term_absent_from_snapshot_is_model_not_measured():
    """A term that describes the clip but is not in circulation is still
    emitted — but it must not borrow the authority of measured evidence."""
    _tags, decisions = ts.select_tags(SCIENCE_CLIP, trending=TRENDING)
    levels = _levels(decisions)
    assert levels["white hole"] == pv.MODEL
    assert all(d.level != pv.MEASURED for d in decisions)


def test_measured_tags_outrank_model_tags():
    _tags, decisions = ts.select_tags(COMEDY_CLIP, trending=TRENDING)
    levels = [d.level for d in decisions if d.level != pv.PRIOR]
    assert levels == sorted(levels, key=lambda l: 0 if l == pv.MEASURED else 1)


# --- the upload-breaking limits -------------------------------------------

def test_total_tag_length_stays_under_youtube_cap():
    """YouTube rejects the whole upload over 500 characters of tags."""
    wordy = {
        "clip_id": "c3",
        "hook_title": " ".join(f"distinctword{i}" for i in range(60)),
        "social_caption": " ".join(f"anotherword{i}" for i in range(60)),
    }
    tags, _ = ts.select_tags(wordy, trending=TRENDING)
    assert sum(len(t) for t in tags) + len(tags) <= ts.MAX_TAG_CHARS


def test_tag_count_never_exceeds_limit_including_structural():
    wordy = {"clip_id": "c4",
             "hook_title": " ".join(f"topicword{i}" for i in range(40))}
    tags, _ = ts.select_tags(wordy, trending=TRENDING)
    assert len(tags) <= ts.MAX_TAGS
    assert "Shorts" in tags


def test_structural_tag_always_ships():
    tags, decisions = ts.select_tags(SCIENCE_CLIP, trending=TRENDING)
    assert "Shorts" in tags
    assert _levels(decisions)["Shorts"] == pv.PRIOR


# --- extraction quality ----------------------------------------------------

def test_stopwords_never_become_tags():
    tags, _ = ts.select_tags(SCIENCE_CLIP, trending=TRENDING)
    for junk in ("are", "we", "a", "our", "inside", "actually", "the"):
        assert junk not in tags


def test_multiword_phrases_are_extracted_and_lead():
    """"white hole" is the search term; "white" and "hole" are debris."""
    tags, _ = ts.select_tags(SCIENCE_CLIP, trending=TRENDING)
    assert "white hole" in tags
    assert tags.index("white hole") < tags.index("white")


def test_subsumed_words_are_demoted_not_dropped():
    """They are worth having as filler, just not ahead of the phrase."""
    tags, _ = ts.select_tags(SCIENCE_CLIP, trending=TRENDING)
    assert "universe" in tags
    assert tags.index("expanding universe") < tags.index("universe")


def test_phrases_do_not_straddle_punctuation():
    clip = {"clip_id": "c5", "hook_title": "Rockets explode. Physics wins."}
    tags, _ = ts.select_tags(clip, trending=TRENDING)
    assert "explode physics" not in tags


# --- case folding ----------------------------------------------------------

def test_case_variants_do_not_split_or_understate_evidence():
    """
    "Minecraft" and "minecraft" arriving as separate rows used to let the
    smaller variant overwrite the larger, understating the sample and
    pushing a strong tag down the ranking.
    """
    split = pd.DataFrame([
        {"tag": "minecraft", "videos": 9, "median_views": 150255},
        {"tag": "Minecraft", "videos": 3, "median_views": 192555},
    ])
    _tags, decisions = ts.select_tags(COMEDY_CLIP, trending=split)
    minecraft = next(d for d in decisions if d.choice == "minecraft")
    assert minecraft.sample == 9


# --- degradation -----------------------------------------------------------

def test_no_snapshot_still_produces_relevant_tags():
    """A ClickHouse outage must not cost the upload its tags."""
    tags, decisions = ts.select_tags(SCIENCE_CLIP, trending=None)
    assert "white hole" in tags
    assert all(d.level != pv.MEASURED for d in decisions)


def test_empty_clip_falls_back_to_structural_only():
    tags, _ = ts.select_tags({}, trending=TRENDING)
    assert tags == ts.STRUCTURAL_TAGS


@pytest.mark.parametrize("field", ["hook_title", "social_caption"])
def test_none_fields_do_not_raise(field):
    ts.select_tags({"clip_id": "c6", field: None}, trending=TRENDING)


# --- channel-declared hints -----------------------------------------------

HINTS = ["funny", "comedy", "ai"]


def test_declared_hints_are_prior_never_measured():
    """
    A channel-brand tag is the owner asserting something about their own
    channel. The trending snapshot can say a term is in circulation; it
    cannot say this video is about it. Labelling "funny" as measured on a
    science clip would be exactly the overstatement this module avoids.
    """
    _tags, decisions = ts.select_tags(
        SCIENCE_CLIP, trending=TRENDING, profile_hints=HINTS)
    funny = next(d for d in decisions if d.choice == "funny")
    assert funny.level == pv.PRIOR
    assert "declared for this channel" in funny.evidence


def test_declared_short_acronym_survives_the_length_floor():
    """MIN_TAG_LEN silently discarded "ai", which is a real tag."""
    tags, _ = ts.select_tags(SCIENCE_CLIP, trending=TRENDING, profile_hints=HINTS)
    assert "ai" in tags


def test_hints_rank_below_terms_the_clip_justified():
    tags, _ = ts.select_tags(SCIENCE_CLIP, trending=TRENDING, profile_hints=HINTS)
    assert tags.index("white hole") < tags.index("funny")


def test_hints_do_not_duplicate_a_term_the_clip_already_earned():
    clip = dict(COMEDY_CLIP)
    tags, decisions = ts.select_tags(clip, trending=TRENDING, profile_hints=["funny"])
    assert tags.count("funny") == 1
    # The clip genuinely is about "funny", so it keeps its measured status.
    assert next(d for d in decisions if d.choice == "funny").level == pv.MEASURED


def test_hints_still_respect_the_character_cap():
    tags, _ = ts.select_tags(
        SCIENCE_CLIP, trending=TRENDING,
        profile_hints=[f"declaredhint{i}" for i in range(40)])
    assert sum(len(t) for t in tags) + len(tags) <= ts.MAX_TAG_CHARS
    assert len(tags) <= ts.MAX_TAGS
