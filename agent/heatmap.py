# agent/heatmap.py
"""
⚡ NornPulse: Which parts of a source people actually re-watch (heatmap.py)
Norn Labs (nornlabs.ai)

Verðandi picks a moment out of a long video by reading the transcript and
watching the footage. That is a judgement, and it is labelled as one. But
for a video with enough views, YouTube has already aggregated what millions
of people actually did — where they scrubbed back, where they replayed —
and publishes it as the "most replayed" graph under the seek bar.

That is measured evidence about this exact video, and it was sitting
unused. The Data API does not expose it; yt-dlp extracts it, as a list of
equal-width buckets each carrying a normalised 0–1 value.

The opening bucket is a lie
---------------------------
The first bucket is 1.0 on essentially every video, because everyone who
presses play sees the first six seconds. It measures *arrival*, not
interest, and a naive "pick the highest bucket" would cut every clip from
the opening — which is the one part of a source most likely to be a title
card, and the part a viewer arriving at a Short has least reason to care
about.

So the opening is dropped before ranking, and the fact that it was dropped
is stated rather than hidden.

What this is not
----------------
Not retention. Retention is per-viewer and owner-only, and this project
reads it for its own channel through the Analytics API. This is re-watch
density on someone else's video: a strong signal about which moments hold
attention, and no signal at all about how many people got there.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from statistics import median
from typing import Any, Dict, List, Optional

logger = logging.getLogger("nornpulse.heatmap")

# Buckets starting inside this fraction of the video are dropped before
# ranking. One bucket would usually do; a couple of percent survives a
# source whose graph is finer-grained.
OPENING_FRACTION = 0.02

# Below this many buckets the graph is too coarse to say anything useful
# about where in a ten-minute video to cut.
MIN_BUCKETS = 20

# A candidate must reach this share of the strongest peak to be reported as
# one. Without it, asking for five peaks on a graph with two returns the two
# and then three arbitrary baseline buckets, which read as five equally
# valid options -- the same error as reporting a number nobody measured.
MIN_PEAK_RATIO = 0.5

# And it must stand this far above the graph's own baseline. The ratio test
# alone cannot see a flat graph, because on one every bucket equals the
# maximum and so every bucket passes -- which would report an evenly-watched
# video as having five outstanding moments.
MIN_PEAK_PROMINENCE = 1.15


@dataclass
class Moment:
    """One stretch of a source, and how heavily it was re-watched."""

    start_sec: float
    end_sec: float
    value: float          # 0-1, normalised by YouTube against this video

    @property
    def mid_sec(self) -> float:
        return (self.start_sec + self.end_sec) / 2

    def as_timestamp(self) -> str:
        m, s = divmod(int(self.start_sec), 60)
        return f"{m:02d}:{s:02d}"


def from_info(info: Dict[str, Any]) -> List[Moment]:
    """Parse yt-dlp's `heatmap` field, or an empty list if absent."""
    raw = info.get("heatmap") or []
    moments = []
    for point in raw:
        try:
            moments.append(Moment(
                start_sec=float(point["start_time"]),
                end_sec=float(point["end_time"]),
                value=float(point["value"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return moments


def fetch(url: str) -> List[Moment]:
    """
    The most-replayed graph for a URL, or an empty list.

    Absent is normal and not an error: YouTube only computes this once a
    video has enough views, so a small channel's upload has none.
    """
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        logger.warning(f"Could not read the most-replayed graph: {str(e)[:120]}")
        return []

    moments = from_info(info)
    if not moments:
        logger.info(
            "No most-replayed graph for this video — YouTube computes one "
            "only after enough views.")
    return moments


def peaks(moments: List[Moment], top_n: int = 5,
          duration_sec: Optional[float] = None,
          window_sec: Optional[float] = None) -> List[Moment]:
    """
    The most re-watched stretches, opening excluded, spread out.

    `window_sec` suppresses neighbours within that distance of a peak
    already chosen. Without it the top five are usually five adjacent
    buckets describing one moment, which looks like five options and is
    one.
    """
    if len(moments) < MIN_BUCKETS:
        if moments:
            logger.info(
                f"Only {len(moments)} heatmap buckets; too coarse to rank.")
        return []

    span = duration_sec or max(m.end_sec for m in moments)
    cutoff = span * OPENING_FRACTION
    ranked = sorted((m for m in moments if m.start_sec >= cutoff),
                    key=lambda m: m.value, reverse=True)

    if window_sec is None:
        # Wide enough that two peaks are genuinely different moments,
        # narrow enough not to blank out half a ten-minute video.
        window_sec = max(span * 0.03, 20.0)

    if not ranked:
        return []
    baseline = median(m.value for m in ranked)
    floor = max(ranked[0].value * MIN_PEAK_RATIO,
                baseline * MIN_PEAK_PROMINENCE)

    chosen: List[Moment] = []
    for candidate in ranked:
        if len(chosen) >= top_n:
            break
        if candidate.value < floor:
            # Ranked descending, so everything after this is baseline too.
            break
        if any(abs(candidate.mid_sec - c.mid_sec) < window_sec for c in chosen):
            continue
        chosen.append(candidate)
    return chosen


def describe(moments: List[Moment], top_n: int = 5,
             duration_sec: Optional[float] = None) -> str:
    """
    The peaks as prompt-ready evidence, or an empty string.

    Written to be pasted into a prompt as *measured* material, so it says
    what the numbers are and what they are not — a model told only "these
    are the best bits" will treat re-watch density as a verdict on quality.
    """
    best = peaks(moments, top_n=top_n, duration_sec=duration_sec)
    if not best:
        return ""

    lines = "\n".join(
        f"    {m.as_timestamp()}  (re-watch {m.value:.2f})" for m in best)
    return (
        "\nMOST RE-WATCHED MOMENTS IN THIS SOURCE (measured — YouTube's own "
        "aggregated re-watch graph for this exact video, normalised 0-1):\n"
        f"{lines}\n"
        "  These are where viewers of the ORIGINAL video scrubbed back to. "
        "Prefer cutting near one of them unless the transcript gives you a "
        "clearly better reason not to. Two caveats you must apply: the graph "
        "measures re-watching, not how many people got there, and the video's "
        "opening has been excluded because it scores 1.0 on every video "
        "whether or not it is interesting.\n"
    )
