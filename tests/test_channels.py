"""
Unit tests for the channel registry.

Nothing here touches YouTube or ClickHouse. The properties worth guarding
are the ones whose failure is expensive and silent: publishing to the wrong
channel, or one channel's re-authorisation quietly overwriting another's
credentials. Both have already happened once on a single-channel setup.
"""

import json

import pytest

from agent import channels as chans


def _write_config(tmp_path, payload):
    path = tmp_path / "channels.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --- the wrong-channel accidents ------------------------------------------

def test_each_channel_gets_its_own_token_path():
    """
    A shared token path means re-authorising channel B silently destroys
    channel A's credentials, and the next publish lands on the wrong
    channel with no error anywhere.
    """
    a = chans.get_channel("nornpulse")
    b = chans.get_channel("sloptokdaily")
    assert a.token_path != b.token_path
    assert b.slug in str(b.token_path)


def test_legacy_token_is_offered_only_to_the_default_channel(tmp_path, monkeypatch):
    """
    The pre-channels token was authorised for one specific channel. Handing
    it to a second channel would publish that channel's clips to the first.
    """
    monkeypatch.setattr(chans, "CREDENTIALS_DIR", tmp_path)
    legacy = tmp_path / "youtube_token.json"
    legacy.write_text("{}", encoding="utf-8")

    default = chans.get_channel(chans.DEFAULT_SLUG)
    other = chans.get_channel("sloptokdaily")

    assert default.resolve_token_path() == legacy
    assert other.resolve_token_path() != legacy


def test_own_token_wins_over_legacy(tmp_path, monkeypatch):
    monkeypatch.setattr(chans, "CREDENTIALS_DIR", tmp_path)
    (tmp_path / "youtube_token.json").write_text("{}", encoding="utf-8")
    own = tmp_path / f"youtube_token_{chans.DEFAULT_SLUG}.json"
    own.write_text("{}", encoding="utf-8")
    assert chans.get_channel(chans.DEFAULT_SLUG).resolve_token_path() == own


def test_unknown_slug_raises_rather_than_falling_back():
    """A typo in --channel must stop the run, not redirect the upload."""
    with pytest.raises(KeyError):
        chans.get_channel("sloptokdailyy")


# --- profile correctness ---------------------------------------------------

def test_comedy_channel_does_not_publish_as_science_and_tech():
    """categoryId was hardcoded to 28 for every upload before channels."""
    assert chans.get_channel("sloptokdaily").profile.category_id == chans.CATEGORY_COMEDY
    assert chans.get_channel("nornpulse").profile.category_id == chans.CATEGORY_SCIENCE_TECH


def test_invalid_category_is_rejected_at_construction():
    with pytest.raises(ValueError):
        chans.ChannelProfile(category_id="9999")


# --- config loading --------------------------------------------------------

def test_config_overrides_builtin_defaults(tmp_path):
    path = _write_config(tmp_path, {"channels": {
        "nornpulse": {"subscribers": 4321, "title": "Renamed"}}})
    channel = chans.get_channel("nornpulse", path=path)
    assert channel.subscribers == 4321
    assert channel.title == "Renamed"
    # Unspecified fields still come from the built-in profile.
    assert channel.profile.category_id == chans.CATEGORY_SCIENCE_TECH


def test_missing_config_falls_back_to_builtins(tmp_path):
    assert chans.load_channels(tmp_path / "absent.json")


def test_malformed_config_does_not_lose_every_channel(tmp_path):
    """Losing the ability to publish anywhere is worse than a stale count."""
    path = tmp_path / "channels.json"
    path.write_text("{not json", encoding="utf-8")
    assert chans.load_channels(path)


def test_one_malformed_channel_does_not_discard_the_others(tmp_path):
    path = _write_config(tmp_path, {"channels": {
        "broken": {"profile": {"category_id": "not-a-category"}},
        "nornpulse": {"subscribers": 7},
    }})
    loaded = chans.load_channels(path)
    assert "broken" not in loaded
    assert loaded["nornpulse"].subscribers == 7


def test_configured_channels_have_real_youtube_ids():
    """A blank id silently makes history ingestion a no-op."""
    for channel in chans.list_channels():
        assert channel.youtube_channel_id.startswith("UC"), channel.slug


# --- editorial constraints reaching the renderer ---------------------------
#
# A reviewer rejected a batch for the music being wrong and one clip for
# being "too bouncy". Both choices came from seeded benchmark priors ranked
# on a generic taxonomy: synthwave/mysterious scores highest overall, so a
# space channel got synthwave, and shake tops the motion table, so shake was
# chosen for every clip. The channel profile carried a music_mood the whole
# time and nothing read it.

def test_a_profile_can_rule_out_a_motion_effect():
    p = chans.ChannelProfile(avoid_motion=["shake"])
    assert p.avoid_motion == ["shake"]


def test_avoiding_nothing_is_the_default():
    assert chans.ChannelProfile().avoid_motion == []


def test_the_constraint_survives_a_round_trip(tmp_path):
    """A profile that loses its constraint on reload has not got one."""
    import json
    path = tmp_path / "channels.json"
    path.write_text(json.dumps({"channels": {"x": {
        "youtube_channel_id": "UC123", "title": "X", "subscribers": 5,
        "profile": {"category_id": "28", "music_mood": "epic",
                    "avoid_motion": ["shake", "punch_in_zoom"]}}}}))
    loaded = chans.load_channels(path)["x"]
    assert loaded.profile.avoid_motion == ["shake", "punch_in_zoom"]
    assert loaded.profile.music_mood == "epic"
    assert loaded.to_dict()["profile"]["avoid_motion"] == ["shake", "punch_in_zoom"]


def test_the_science_channel_bans_shake_and_asks_for_epic():
    """The two settings that answer the actual review comments."""
    c = chans.get_channel("nornpulse")
    assert "shake" in c.profile.avoid_motion
    assert c.profile.music_mood == "epic"


def test_the_comedy_channel_is_still_allowed_to_wobble():
    assert chans.get_channel("sloptokdaily").profile.avoid_motion == []


def test_excluding_every_unfilled_mode_lands_on_the_filled_one():
    """
    A 16:9 source cannot both fill a 9:16 frame and keep its full width.
    This channel's reviewer chose filling, three times over -- "completely
    broken" for blurred_background, "too much unused space in the bottom"
    for top_anchored_crop, "better" for center_crop.

    So every unfilled mode is excluded, and when a segment also excludes the
    cropping ones there is nothing left at all. That is safe rather than
    broken: the benchmark returns nothing and the renderer falls back to
    center_crop. Pinned because it reads like an accident, and because a
    future default of anything else would silently reintroduce the letterbox
    on exactly the clips that were rejected for it.
    """
    profile = chans.get_channel("nornpulse").profile
    for unfilled in ("blurred_background", "top_anchored_crop", "cinematic_letterbox"):
        assert unfilled in profile.avoid_crop
    assert "center_crop" not in profile.avoid_crop


def test_the_renderer_defaults_to_a_filled_frame():
    """The fallback the exclusion above relies on."""
    import inspect
    from agent.verdandi_orchestrator import VerdandiADK
    source = inspect.getsource(VerdandiADK._make_tools)
    assert 'visual_benchmark.get("crop_mode", "center_crop")' in source
    assert 'if visual_benchmark else "center_crop"' in source
