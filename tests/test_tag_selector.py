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


# --- long-run fragments ----------------------------------------------------

def test_long_content_runs_do_not_manufacture_prose_fragments():
    """
    A run of content words longer than a phrase is a sentence with its
    function words stripped. Sliding a window along it invents terms nobody
    searches for — "another cursed one", "pile ai slop" — which look
    automated and consume slots real terms need.
    """
    clip = {
        "clip_id": "c7",
        "hook_title": "AI slop goes completely off the rails",
        "social_caption": "another cursed one from the daily pile",
    }
    tags, _ = ts.select_tags(clip, trending=TRENDING)
    for fragment in ("rails another cursed", "pile ai slop", "daily pile ai",
                     "another cursed one", "rails another"):
        assert fragment not in tags
    # The individual topic words still survive.
    assert "slop" in tags and "cursed" in tags


def test_short_runs_still_yield_their_phrase():
    """The long-run guard must not cost us "white hole"."""
    tags, _ = ts.select_tags(SCIENCE_CLIP, trending=TRENDING)
    assert "white hole" in tags
    assert "expanding universe" in tags


def test_no_tag_exceeds_the_phrase_word_limit():
    clip = {"clip_id": "c8",
            "hook_title": "quantum vacuum decay bubble nucleation cascade event"}
    tags, _ = ts.select_tags(clip, trending=TRENDING)
    for tag in tags:
        assert len(tag.split()) <= ts.MAX_PHRASE_WORDS, tag


def test_intermediate_windows_are_not_emitted():
    """
    "Florida humidity claims another fan" gives the run
    [florida, humidity, claims]. The whole phrase is a search term and the
    single words are a fallback, but "humidity claims" is a slice out of the
    middle of a sentence, sitting in the tag list beside the phrase it was
    cut from.
    """
    clip = {"clip_id": "c9", "hook_title": "Florida humidity claims another fan"}
    tags, _ = ts.select_tags(clip, trending=TRENDING)
    assert "florida humidity claims" in tags
    assert "florida" in tags
    for slice_ in ("humidity claims", "florida humidity"):
        assert slice_ not in tags


# --------------------------------------------------------------------------
# Real regression: ncSGySusHUg's published tags
# --------------------------------------------------------------------------
# "moon's harshest environment", nasa, thermal swing, lunar, south, pole,
# experiences, extreme, temperatures, builds, landing, Shorts -- four
# distinct problems in one real published tag set (see BACKLOG.md).

NASA_CLIP = {
    "clip_id": "c10",
    "hook_title": "NASA's Plan For A Permanent Moon Base",
    "social_caption": (
        "In the moon's harshest environment, a massive thermal swing at "
        "the lunar south pole experiences extreme temperatures as NASA "
        "builds its next landing site."),
    "topic_category": "space",
}


def test_a_long_unbroken_run_still_yields_its_real_sub_phrases():
    """
    "lunar south pole experiences extreme temperatures" is six content
    words with no stopword between them -- one run, past
    MAX_PHRASE_WORDS, which used to mean it degraded entirely to loose
    words and lost "lunar south pole" and "extreme temperatures" as
    phrases even though both are genuine three-and-two-word search terms
    sitting right there inside it.
    """
    tags, _ = ts.select_tags(NASA_CLIP, trending=None)
    assert "lunar south pole" in tags
    assert "extreme temperatures" in tags


def test_purely_connective_verbs_never_ship_as_bare_tags():
    tags, _ = ts.select_tags(NASA_CLIP, trending=None)
    assert "experiences" not in tags
    assert "builds" not in tags


def test_a_word_that_is_sometimes_a_real_topic_is_not_blocklisted():
    """
    Unlike "experiences"/"builds", "landing" is a legitimate topic word in
    plenty of real content (a landing site, a moon landing) -- the fix is
    to stop the run degrading around it, not to blocklist it outright.
    Once "builds" breaks the run properly, "landing site" forms as its own
    real phrase rather than needing "landing" banned to avoid an orphan.
    """
    tags, _ = ts.select_tags(NASA_CLIP, trending=None)
    assert "landing site" in tags


def test_zero_measured_tags_gets_an_explanatory_note_not_silence():
    """
    Every tag on this clip is genuinely MODEL -- the (comedy/gaming)
    TRENDING fixture has nothing space-related to match. Left silent, an
    all-MODEL tag list reads as "validation didn't run"; it should read as
    "validation ran, the snapshot just doesn't cover this subject".
    """
    _, decisions = ts.select_tags(NASA_CLIP, trending=TRENDING)
    notes = [d for d in decisions if d.choice == "(no tag matched the trending snapshot)"]
    assert len(notes) == 1
    assert notes[0].level == pv.MODEL


def test_the_explanatory_note_does_not_appear_when_something_matched():
    tags, decisions = ts.select_tags(COMEDY_CLIP, trending=TRENDING)
    assert any(d.level == pv.MEASURED for d in decisions if d.step == "Tag")
    notes = [d for d in decisions if d.choice == "(no tag matched the trending snapshot)"]
    assert notes == []


def test_the_explanatory_note_does_not_appear_without_a_snapshot_to_check():
    """
    No trending data at all is a different, already-obvious problem
    (there's nothing to validate against, full stop) -- conflating it with
    "validated and found nothing" would muddy a distinct failure mode.
    """
    _, decisions = ts.select_tags(NASA_CLIP, trending=None)
    notes = [d for d in decisions if d.choice == "(no tag matched the trending snapshot)"]
    assert notes == []
