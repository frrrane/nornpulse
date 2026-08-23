# agent/provenance.py
"""
⚡ NornPulse: Decision provenance (provenance.py)
Norn Labs (nornlabs.ai)

Says, for one finished clip, where each decision came from and how good
the evidence behind it is.

The pipeline makes six or seven choices per clip — hook, cut, captions,
framing, motion, colour, score — and until now they arrived as a flat list
of values with no indication of which were measured against real data and
which came from a table someone typed. Those are very different claims,
and presenting them identically overstates the weaker ones.

Three provenance levels:

  MEASURED  — read from the materialised global facts: real YouTube
              outcomes, within this channel's size band, with a sample
              size attached.
  PRIOR     — from a seeded benchmark table. Sixteen hand-written rows
              are a starting assumption, not evidence, and the UI should
              not let them look like evidence.
  MODEL     — Verðandi's own judgement about this specific transcript.
              Not grounded in anything external, and not pretending to be.

The honest answer for visual treatment and music is PRIOR: the public
dataset has no crop mode, camera motion, colour grade or audio features,
so there is nothing to ground them against. Saying so is better than
letting them sit next to the measured hook figure looking equally solid.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pandas as pd

MEASURED = "measured"
PRIOR = "prior"
MODEL = "model"

LEVEL_LABEL = {
    MEASURED: "Measured",
    PRIOR: "Seeded prior",
    MODEL: "Model judgement",
}


@dataclass
class Decision:
    """One choice the pipeline made, and the basis for it."""
    step: str
    choice: str
    level: str
    evidence: str
    sample: Optional[int] = None

    @property
    def label(self) -> str:
        return LEVEL_LABEL.get(self.level, self.level)


def _hook_decision(clip: Dict[str, Any], band: str,
                   facts: Optional[pd.DataFrame]) -> Optional[Decision]:
    from agent import global_benchmarks as gb

    hook = clip.get("hook_type")
    if not hook:
        return None
    rows = gb.hook_benchmarks(band, facts=facts)
    row = rows[rows["bucket"] == hook] if not rows.empty else pd.DataFrame()
    if row.empty:
        # visual_disruption and anything else not inferable from a title
        # has no global figure. Say that rather than implying one.
        return Decision(
            "Hook", hook, PRIOR,
            "No global measurement — this hook type cannot be identified from a "
            "video title, so it is ranked from the seeded benchmarks only.")

    row = row.iloc[0]
    plain = rows[rows["bucket"] == "plain"]
    lift = ""
    if not plain.empty and float(plain.iloc[0]["median_views"]):
        pct = (float(row["median_views"]) / float(plain.iloc[0]["median_views"]) - 1) * 100
        lift = f", {pct:+.0f}% against an unstyled title"
    return Decision(
        "Hook", hook, MEASURED,
        f"{row['median_views']:,.0f} median views for {band}-subscriber channels{lift}",
        int(row["sample_videos"]))


def _subtitle_decision(clip: Dict[str, Any], band: str,
                       facts: Optional[pd.DataFrame]) -> Optional[Decision]:
    from agent import global_benchmarks as gb

    if not clip.get("has_subtitles"):
        return None
    language = clip.get("caption_language")
    choice = f"burned in{f' · translated to {language}' if language else ''}"
    lift = gb.subtitle_lift(band, facts=facts)
    if not lift:
        return Decision("Captions", choice, PRIOR,
                        "Global caption data not materialised.")
    views = ("no measurable reach lift at this channel size"
             if lift["views_lift_pct"] < 1
             else f"{lift['views_lift_pct']:+.0f}% median views")
    like = (f", {lift['like_lift_pct']:+.0f}% like rate"
            if lift["like_lift_pct"] is not None else "")
    return Decision("Captions", choice, MEASURED,
                    f"{views}{like} for {band}-subscriber channels",
                    lift["sample_videos"])


def _treatment_decisions(clip: Dict[str, Any]) -> List[Decision]:
    """
    Framing, motion and colour grade.

    All PRIOR, and not for want of trying: the public dataset carries no
    visual features at all, so there is nothing external to measure these
    against. They come from visual_style_benchmarks, which is sixteen
    seeded rows keyed on hook type.
    """
    out = []
    for step, key in (("Framing", "crop_mode"),
                      ("Camera motion", "motion_effect"),
                      ("Colour grade", "color_grade")):
        value = clip.get(key)
        if value:
            out.append(Decision(
                step, value, PRIOR,
                "From visual_style_benchmarks, a seeded table keyed on hook type. "
                "The public dataset has no visual features, so this is not "
                "measured against real outcomes."))
    return out


def _music_decision(clip: Dict[str, Any]) -> Optional[Decision]:
    if not clip.get("has_bragi_score"):
        return None
    genre = clip.get("music_genre") or "original score"
    mood = clip.get("music_mood")
    return Decision(
        "Score", f"{genre}{f' · {mood}' if mood else ''}", PRIOR,
        "From music_virality_benchmarks, a seeded table. The public dataset "
        "carries no audio features, so this is a starting assumption rather "
        "than a measured effect.")


def _cut_decision(clip: Dict[str, Any]) -> Optional[Decision]:
    start, end = clip.get("start_time"), clip.get("end_time")
    if not start or not end:
        return None
    return Decision(
        "Cut", f"{start}–{end}", MODEL,
        "Verðandi's reading of this transcript, clamped to the requested "
        "duration and any cut range you set.")


def _reach_decision(clip: Dict[str, Any], band: str, subscribers: int,
                    facts: Optional[pd.DataFrame]) -> Optional[Decision]:
    from agent import global_benchmarks as gb

    forecast = gb.forecast_reach(
        subscribers, has_subtitles=bool(clip.get("has_subtitles")), facts=facts)
    if not forecast:
        return None
    return Decision(
        "Forecast reach", f"{forecast['p50']:,.0f} views (p50)", MEASURED,
        f"p10–p90 {forecast['p10']:,.0f}–{forecast['p90']:,.0f} for "
        f"{band}-subscriber channels",
        forecast["sample_videos"])


def decisions_for_clip(clip: Dict[str, Any], subscribers: int = 0,
                       facts: Optional[pd.DataFrame] = None) -> List[Decision]:
    """Every decision behind one clip, in the order the pipeline made them."""
    from agent import global_benchmarks as gb

    band = gb.size_band_for(subscribers)
    candidates = [
        _hook_decision(clip, band, facts),
        _cut_decision(clip),
        _subtitle_decision(clip, band, facts),
        *_treatment_decisions(clip),
        _music_decision(clip),
        _reach_decision(clip, band, subscribers, facts),
    ]
    return [d for d in candidates if d is not None]


def grounding_summary(decisions: List[Decision]) -> Dict[str, int]:
    counts = {MEASURED: 0, PRIOR: 0, MODEL: 0}
    for d in decisions:
        counts[d.level] = counts.get(d.level, 0) + 1
    return counts
