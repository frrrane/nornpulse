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

    def __init__(self, search_items, video_items):
        self._search_items = search_items
        self._video_items = video_items
        self.search_kwargs = {}
        self.videos_kwargs = {}

    def search(self):
        outer = self

        class _S:
            def list(self, **kw):
                outer.search_kwargs = kw
                return type("R", (), {"execute": lambda s: {"items": outer._search_items}})()
        return _S()

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
