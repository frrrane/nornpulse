"""
Unit tests for the current-trending ingestion layer.

No API is called. What matters here is the distinction between the two
sources: YouTube's trending chart is authoritative and almost entirely
long-form, and there is no Shorts equivalent of it, so the Shorts path is a
search and must never be reported as the same thing.
"""

import pytest

from agent import trending_ingest as ti



# --- collecting actual Shorts ----------------------------------------------
#
# The trending chart is long-form. A snapshot taken while writing this held
# 49 videos, none of them Shorts, median length ten and a half minutes --
# so a Shorts pipeline grounded in it was choosing topics from let's-plays
# and album visualisers. There is no Shorts chart in the API, so this is a
# search, and it is stored under its own source so the two claims can never
# be pooled.

class _FakeYouTube:
    """Minimal stand-in for the discovery client."""

    def __init__(self, search_items, video_items, channel_items=None):
        self._search_items = search_items
        self._video_items = video_items
        self._channel_items = channel_items or []
        self.search_kwargs = {}
        self.videos_kwargs = {}
        self.channel_batches = []

    def search(self):
        outer = self

        class _S:
            def list(self, **kw):
                outer.search_kwargs = kw
                return type("R", (), {"execute": lambda s: {"items": outer._search_items}})()
        return _S()

    def channels(self):
        outer = self

        class _C:
            def list(self, **kw):
                outer.channel_batches.append(kw.get("id", "").split(","))
                return type("R", (), {"execute": lambda s: {"items": outer._channel_items}})()
        return _C()

    def videos(self):
        outer = self

        class _V:
            def list(self, **kw):
                outer.videos_kwargs = kw
                return type("R", (), {"execute": lambda s: {"items": outer._video_items}})()
        return _V()


def _video(vid, seconds, title="A short"):
    mins, secs = divmod(seconds, 60)
    return {
        "id": vid,
        "snippet": {"title": title, "channelTitle": "c", "channelId": "cid",
                    "categoryId": "23", "publishedAt": "2026-08-25T00:00:00Z",
                    "tags": ["funny"]},
        "statistics": {"viewCount": "100", "likeCount": "5", "commentCount": "1"},
        "contentDetails": {"duration": f"PT{mins}M{secs}S"},
        "topicDetails": {},
    }


def _search_hit(vid):
    return {"id": {"videoId": vid}}


def test_a_query_is_always_sent():
    """
    search.list with duration, ordering and region but no q returns an
    empty list rather than an error -- verified against the live API. A
    missing default would look like "no Shorts are trending".
    """
    yt = _FakeYouTube([_search_hit("a")], [_video("a", 30)])
    ti.fetch_trending_shorts(yt, region="US")
    assert yt.search_kwargs.get("q") == ti.DEFAULT_SHORTS_QUERY


def test_an_explicit_query_wins():
    yt = _FakeYouTube([_search_hit("a")], [_video("a", 30)])
    ti.fetch_trending_shorts(yt, region="US", query="#aislop")
    assert yt.search_kwargs["q"] == "#aislop"


def test_results_over_sixty_seconds_are_dropped():
    """The API's own 'short' filter means under four minutes, not a Short."""
    yt = _FakeYouTube(
        [_search_hit("a"), _search_hit("b")],
        [_video("a", 30), _video("b", 200, "nearly four minutes")])
    rows = ti.fetch_trending_shorts(yt, region="US")
    assert [r["video_id"] for r in rows] == ["a"]


def test_shorts_rows_are_labelled_with_their_own_source():
    """Pooling a search with the trending chart would overstate both."""
    yt = _FakeYouTube([_search_hit("a")], [_video("a", 30)])
    rows = ti.fetch_trending_shorts(yt, region="US")
    assert rows[0]["source"] == ti.SOURCE_SHORTS_SEARCH
    assert ti.SOURCE_SHORTS_SEARCH != ti.SOURCE_CHART


def test_chart_rows_keep_the_chart_source():
    yt = _FakeYouTube([], [_video("a", 700)])
    rows = ti.fetch_trending(yt, region="US")
    assert rows[0]["source"] == ti.SOURCE_CHART


def test_no_search_results_is_not_a_crash():
    yt = _FakeYouTube([], [])
    assert ti.fetch_trending_shorts(yt, region="US") == []


# --- channel size, so the snapshot can be stratified ------------------------
#
# Without it the live layer cannot be compared like for like. A snapshot of
# the most-watched Shorts has a median around nine million views; the channel
# this project publishes to has a median of a few hundred. Grounding one in
# the other reproduces, inside the system's own evidence, exactly the advice
# asymmetry it exists to correct.

def _channel(cid, subs, hidden=False):
    stats = {"subscriberCount": str(subs)}
    if hidden:
        stats = {"hiddenSubscriberCount": True}
    return {"id": cid, "statistics": stats}


def test_a_videos_channel_size_is_recorded_and_banded():
    yt = _FakeYouTube([_search_hit("a")], [_video("a", 30)],
                      [_channel("cid", 250)])
    rows = ti.fetch_trending_shorts(yt, region="US")
    assert rows[0]["channel_subscribers"] == 250
    assert rows[0]["size_band"] == "100-1k"


def test_an_unknown_size_is_blank_not_small():
    """
    A channel we failed to read is not the same claim as a small channel,
    and banding it as one would pollute the band that matters most here.
    """
    yt = _FakeYouTube([_search_hit("a")], [_video("a", 30)], [])
    rows = ti.fetch_trending_shorts(yt, region="US")
    assert rows[0]["size_band"] == ""
    assert rows[0]["channel_subscribers"] == 0


def test_a_hidden_subscriber_count_is_unknown_not_zero():
    yt = _FakeYouTube([_search_hit("a")], [_video("a", 30)],
                      [_channel("cid", 0, hidden=True)])
    rows = ti.fetch_trending_shorts(yt, region="US")
    assert rows[0]["size_band"] == ""


def test_sizes_are_requested_in_batches_of_fifty():
    """channels.list caps at 50 ids; a 51st would be silently dropped."""
    yt = _FakeYouTube([], [], [])
    ti.fetch_channel_sizes(yt, [f"c{i}" for i in range(120)])
    assert [len(b) for b in yt.channel_batches] == [50, 50, 20]


def test_duplicate_channels_are_asked_for_once():
    yt = _FakeYouTube([], [], [])
    ti.fetch_channel_sizes(yt, ["c1", "c1", "c2", "", "c1"])
    assert yt.channel_batches == [["c1", "c2"]]


def test_a_size_lookup_failure_does_not_lose_the_snapshot():
    """Losing the band is a shame; losing the videos is worse."""
    class _Broken(_FakeYouTube):
        def channels(self):
            raise RuntimeError("quota exceeded")

    yt = _Broken([_search_hit("a")], [_video("a", 30)])
    rows = ti.fetch_trending_shorts(yt, region="US")
    assert len(rows) == 1 and rows[0]["size_band"] == ""


@pytest.mark.parametrize("subs,band", [
    (0, "0-100"), (99, "0-100"), (100, "100-1k"), (999, "100-1k"),
    (1000, "1k-10k"), (50_000, "10k-100k"), (2_000_000, "1M+"),
])
def test_banding_matches_the_canonical_thresholds(subs, band):
    """One banding function, or two layers silently disagree about 'small'."""
    from agent.global_benchmarks import size_band_for
    assert size_band_for(subs) == band
