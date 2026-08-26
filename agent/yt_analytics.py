# agent/yt_analytics.py
"""
⚡ NornPulse: The owner's view of a video (yt_analytics.py)
Norn Labs (nornlabs.ai)

Everything else in this project reads the *public* numbers — view count,
likes, comments — because that is all the Data API exposes about anyone's
video. Those answer "how did this do". They cannot answer "why".

The YouTube Analytics API is a different API, available only to the channel
owner, and it answers the second question. The difference is the difference
between:

    this got 343 views

and

    this was shown 4,000 times, 3% of people clicked, and half of those
    who did left before the second second

One of those tells you the thumbnail is fine and the opening is not. The
other tells you nothing you can act on.

That matters most at small scale. A channel with a few hundred views per
video has view counts dominated by distribution luck — this project
measured its own comedy channel and found views and like rate correlating
at +0.13, near enough to zero that the public number carries almost no
information. Retention is measured *per viewer who arrived*, so it is not
diluted by how many arrived.

Why it needs a re-authorisation
-------------------------------
This needs the `yt-analytics.readonly` scope, which the existing tokens do
not carry — they were issued for upload and public read only. Adding it
means re-running the OAuth flow for every channel. There is no way around
that: scopes are fixed when a token is granted.

Honest about thin data
----------------------
YouTube suppresses reports built on very few viewers, and returns an empty
row set rather than an error when it does. An empty result therefore means
"not enough people watched for this to be reportable", which is a real and
likely answer for a channel of this size — so it is reported as that,
rather than as a zero.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("nornpulse.yt_analytics")

ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"

# Sampled across the video's length as a fraction, so the curve is
# comparable between clips of different durations.
RETENTION_DIMENSION = "elapsedVideoTimeRatio"

# Where a Short is won or lost. Anything before this is the hook.
HOOK_WINDOW_RATIO = 0.15


class AnalyticsUnavailable(RuntimeError):
    """
    Raised when the API cannot be reached at all.

    Distinct from an empty result, which is not an error: the API returning
    no rows means too few people watched for a report to exist, and that is
    an answer.
    """


def _client(credentials):
    from googleapiclient.discovery import build
    return build("youtubeAnalytics", "v2", credentials=credentials)


def _query(credentials, **params) -> Dict[str, Any]:
    """
    Run one report, translating the failures that have a specific fix.

    Both of the common ones are 403s with completely different remedies,
    and matching on the status alone gets it wrong: the first version of
    this told a correctly-scoped token to re-authorise, when the real
    problem was an API that had never been switched on. A confident wrong
    diagnosis costs more than no diagnosis, so the message has to come from
    the text, not the code.
    """
    try:
        return _client(credentials).reports().query(**params).execute()
    except Exception as e:
        message = str(e)
        lowered = message.lower()

        if "has not been used in project" in lowered or "it is disabled" in lowered:
            raise AnalyticsUnavailable(
                "The YouTube Analytics API is not enabled on this Google "
                "Cloud project. Enable youtubeanalytics.googleapis.com and "
                "retry — this is a project setting and has nothing to do "
                "with the token or its scopes.") from e

        if "insufficient authentication scopes" in lowered or \
                "scope_insufficient" in lowered:
            raise AnalyticsUnavailable(
                "The token does not carry the yt-analytics.readonly scope. "
                "Re-run the OAuth flow for this channel: scopes are fixed "
                "when a token is granted, so an existing token cannot gain "
                "one.") from e

        raise AnalyticsUnavailable(message[:300]) from e


def summary(credentials, start_date: str, end_date: str,
            video_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    How a video, or the whole channel, was actually watched.

    Returns None when the report comes back empty, which means too few
    viewers for YouTube to report on rather than zero of anything.
    """
    params = {
        "ids": "channel==MINE",
        "startDate": start_date,
        "endDate": end_date,
        "metrics": ("views,estimatedMinutesWatched,averageViewDuration,"
                    "averageViewPercentage"),
    }
    if video_id:
        params["filters"] = f"video=={video_id}"

    response = _query(credentials, **params)
    rows = response.get("rows") or []
    if not rows:
        logger.info(
            f"No analytics rows for {video_id or 'the channel'} between "
            f"{start_date} and {end_date} — too few viewers to report on.")
        return None

    headers = [h["name"] for h in response.get("columnHeaders", [])]
    report = dict(zip(headers, rows[0]))

    # A row of zeros is absent data wearing the shape of an answer. YouTube
    # returns one for a video too new to have been processed -- analytics
    # lag reporting by a day or more -- and treating it as a measurement
    # produces "the average viewer watched 0% of it" about a video nobody
    # has had the chance to watch. That is precisely the fabricated finding
    # this module exists to refuse.
    if not report.get("views"):
        logger.info(
            f"{video_id or 'the channel'} reports zero views for "
            f"{start_date}..{end_date}. Treating as not yet reported rather "
            f"than as measured zeros; analytics lag publication by a day or "
            f"more.")
        return None

    return report


def retention_curve(credentials, video_id: str,
                    start_date: str, end_date: str) -> List[Dict[str, float]]:
    """
    The share of viewers still watching at each point through the video.

    The single most diagnostic thing available about a Short. A clip that
    loses half its viewers in the first fifteen percent has an opening
    problem; one that holds them to the end and gets no likes has a
    different problem entirely, and the public numbers cannot tell those
    apart.

    Returns an empty list when YouTube has too little data, which for a
    video with a few hundred views is the expected outcome rather than a
    failure.
    """
    response = _query(
        credentials,
        ids="channel==MINE",
        startDate=start_date,
        endDate=end_date,
        metrics="audienceWatchRatio,relativeRetentionPerformance",
        dimensions=RETENTION_DIMENSION,
        filters=f"video=={video_id}",
    )
    rows = response.get("rows") or []
    if not rows:
        logger.info(f"No retention curve for {video_id}: too few viewers.")
        return []

    headers = [h["name"] for h in response.get("columnHeaders", [])]
    return [dict(zip(headers, row)) for row in rows]


def hook_retention(curve: List[Dict[str, float]],
                   window: float = HOOK_WINDOW_RATIO) -> Optional[float]:
    """
    The share still watching at the end of the hook window.

    Reported as a fraction of the viewers who started, so it is comparable
    between a twelve-second clip and a nine-minute one — which is the whole
    reason the curve is sampled by ratio rather than by seconds.
    """
    if not curve:
        return None
    within = [p for p in curve if p.get(RETENTION_DIMENSION, 0) <= window]
    if not within:
        return None
    last = max(within, key=lambda p: p[RETENTION_DIMENSION])
    return float(last.get("audienceWatchRatio", 0.0))


def diagnose(credentials, video_id: str, start_date: str,
             end_date: str) -> Dict[str, Any]:
    """
    What this video's own numbers say about why it did what it did.

    Deliberately returns findings rather than a score. "Lost 60% in the
    first second" is actionable; a 7 out of 10 is not.
    """
    result: Dict[str, Any] = {
        "video_id": video_id,
        "reportable": False,
        "findings": [],
    }

    try:
        overview = summary(credentials, start_date, end_date, video_id)
        curve = retention_curve(credentials, video_id, start_date, end_date)
    except AnalyticsUnavailable as e:
        result["findings"].append(f"analytics could not be read: {e}")
        return result

    if not overview and not curve:
        result["findings"].append(
            "nothing reported yet — either too few viewers for YouTube to "
            "report on, or too recently published for it to have processed "
            "them. Neither is a measurement of zero.")
        return result

    result["reportable"] = True
    result["overview"] = overview
    result["curve_points"] = len(curve)

    kept = hook_retention(curve)
    if kept is not None:
        result["hook_retention"] = round(kept, 3)
        if kept < 0.5:
            result["findings"].append(
                f"{(1 - kept) * 100:.0f}% left during the hook — the opening "
                f"is the problem, not the topic")
        elif kept > 0.8:
            result["findings"].append(
                f"the hook held {kept * 100:.0f}% — whatever lost viewers "
                f"happened later, or they watched and did not engage")

    if overview and overview.get("averageViewPercentage") is not None:
        pct = float(overview["averageViewPercentage"])
        result["findings"].append(
            f"the average viewer watched {pct:.0f}% of it")

    return result
