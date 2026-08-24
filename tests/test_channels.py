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
