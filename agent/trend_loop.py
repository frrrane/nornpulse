# agent/trend_loop.py
"""
⚡ NornPulse: Trend-driven generation (trend_loop.py)
Norn Labs (nornlabs.ai)

Read what is trending, decide what this channel could make about it, make
it, and take it through the same grounding every other clip gets.

This is the loop that makes the system an agent rather than a tool. Every
other path starts from a video someone already had. This one starts from
the current state of the world and ends with a published Short, and the
only human step is the approval that was always there.

What it is careful about
------------------------
**Copyright.** The trending snapshot is a list of what is popular, not a
source of footage. Frames come from `agent.footage`, which generates them
or takes them from freely-licensed archives. Nothing is ever re-cut from a
trending video.

**Provenance.** A topic being trending is measured — the snapshot says how
many videos carry it and what they got. Whether *this channel* could make
something good about it is a model judgement and nothing more. Those two
claims are recorded separately, because collapsing them would let a
confident guess inherit the authority of a real count.

**Relevance.** A comedy channel is not obliged to have an opinion about
every trending term. If nothing in the snapshot suits the channel, the
honest output is no topic, and the loop stops rather than forcing one.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from agent import provenance as pv

logger = logging.getLogger(__name__)

MODEL = "gemini-3.6-flash"

# How many trending tags the model is asked to choose between. Enough for a
# real choice, few enough that the prompt stays cheap.
TOPIC_SHORTLIST = 40

# A tag carried by only one or two trending videos is noise rather than a
# trend, and grounding a whole video in it would overstate the evidence.
MIN_TREND_VIDEOS = 2


@dataclass
class Brief:
    """What to make, and why this channel is making it."""

    topic: str
    angle: str                      # the comedic or editorial take
    video_prompt: str               # what actually goes to the generator
    title: str
    caption: str
    hook_type: str = ""
    negative_prompt: str = ""
    trend_videos: int = 0           # measured: trending videos carrying the topic
    trend_median_views: float = 0.0
    rationale: str = ""             # model judgement, labelled as such
    extra: Dict[str, Any] = field(default_factory=dict)

    def as_clip(self, clip_id: str) -> Dict[str, Any]:
        """The clip dict the tag selector and publisher expect."""
        return {
            "clip_id": clip_id,
            "hook_title": self.title,
            "social_caption": self.caption,
            "hook_type": self.hook_type,
            "topic_category": self.topic,
            "has_subtitles": False,
        }

    def decisions(self) -> List[pv.Decision]:
        """Where each half of this brief came from."""
        out = [
            pv.Decision(
                step="Topic",
                choice=self.topic,
                level=pv.MEASURED,
                evidence=(f"carried by {self.trend_videos} currently-trending videos, "
                          f"median {self.trend_median_views:,.0f} views"),
                sample=self.trend_videos,
            ),
            pv.Decision(
                step="Angle",
                choice=self.angle,
                level=pv.MODEL,
                evidence=self.rationale or "the model's own judgement about this channel",
            ),
        ]
        return out


def candidate_topics(trending: pd.DataFrame,
                     limit: int = TOPIC_SHORTLIST) -> List[Dict[str, Any]]:
    """
    Trending tags worth considering, strongest first.

    Filtered by how many videos actually carry the tag: a term on one video
    is not a trend, and treating it as one would put a MEASURED label on
    something close to noise.
    """
    if trending is None or trending.empty:
        return []
    df = trending.copy()
    if "videos" in df.columns:
        df = df[df["videos"] >= MIN_TREND_VIDEOS]
    if df.empty:
        return []
    df = df.sort_values(
        ["videos", "median_views"], ascending=False).head(limit)
    return [
        {
            "tag": str(r["tag"]),
            "videos": int(r.get("videos", 0) or 0),
            "median_views": float(r.get("median_views", 0) or 0),
        }
        for _, r in df.iterrows()
    ]


def _json_from(text: str) -> Optional[Dict[str, Any]]:
    """Pull the first JSON object out of a model response."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        start = text.find("{")
        end = text.rfind("}")
        raw = text[start:end + 1] if start != -1 and end > start else None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


_BRIEF_PROMPT = """You are planning ONE short vertical video for a specific \
YouTube channel, based on what is trending right now.

CHANNEL
  name: {title}
  what it publishes: {hints}
  tone: {tone}

CURRENTLY TRENDING (measured — tag, how many trending videos carry it, their median views)
{topics}

YOUR TASK
Pick the ONE trending topic this channel could most plausibly make a video \
about, and design that video.

Hard rules:
- Do NOT pick a topic just because it is the most popular. Pick the one this \
channel can actually speak to. A gaming tag is a bad fit for a channel that \
does not cover gaming.
- The video must be generated from scratch by a text-to-video model. Do not \
reference, recreate, or describe specific copyrighted characters, real \
identifiable people, logos, or brands. Describe an original scene.
- It is 8 seconds long and vertical. One clear visual idea, not a montage.
- If NOTHING in the list genuinely suits this channel, return \
{{"suitable": false, "why": "..."}} and nothing else. That is a valid answer \
and is better than forcing a bad fit.

MAKING IT ACTUALLY FUNNY (if this channel is a comedy channel)
A cute animal doing a human activity is not a joke. It is a stock image with \
motion, and it is what every AI video looks like. Do better than that:
- The humour needs a TURN — something that is one thing for three seconds and \
then reveals itself as another. Escalation, an unexpected consequence, a \
reaction shot that recontextualises what came before.
- Be SPECIFIC. "A dog in a suit" is nothing. "A dog in an ill-fitting suit \
sweating through a performance review it clearly organised itself" is a joke, \
because the detail implies a whole situation.
- The funniest thing on screen should be a BEHAVIOUR, not a costume.
- Commit to one absurd premise completely and play it dead straight. Comedy \
comes from the seriousness of the treatment, not from signalling that it is \
meant to be silly.
- Say what happens across the eight seconds, not just what is in frame. A \
video prompt that describes a static tableau produces a static tableau.

Return ONLY JSON:
{{
  "suitable": true,
  "topic": "<the exact tag you picked, copied from the list>",
  "angle": "<the comedic or editorial take, one sentence>",
  "video_prompt": "<a vivid, self-contained prompt for a text-to-video model: \
subject, action, setting, camera, lighting, mood. No named characters or brands.>",
  "negative_prompt": "<what the generator should avoid, comma separated>",
  "title": "<YouTube title, under 80 characters>",
  "caption": "<one-line description>",
  "hook_type": "<one of: shock_stat, curiosity_gap, contrarian_claim, \
problem_agitation, direct_question, visual_disruption, metaphor_analogy, \
story_in_medias_res>",
  "rationale": "<why this topic suits THIS channel, one sentence>"
}}"""


def write_brief(channel, topics: List[Dict[str, Any]],
                tone: str = "", api_key: Optional[str] = None,
                model: str = MODEL) -> Optional[Brief]:
    """
    Ask the model to pick a topic and design a video for it.

    Returns None when the model judges that nothing trending suits this
    channel. That is a real answer, not a failure — a comedy channel is not
    obliged to have a take on whatever happens to be popular today, and
    forcing one produces exactly the off-brand filler this loop exists to
    avoid.
    """
    from google import genai

    key = api_key or os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set.")
    if not topics:
        logger.warning("No trending topics to choose from.")
        return None

    listed = "\n".join(
        f"  - {t['tag']}  ({t['videos']} videos, median {t['median_views']:,.0f} views)"
        for t in topics)
    prompt = _BRIEF_PROMPT.format(
        title=channel.title,
        hints=", ".join(channel.profile.topic_hints) or "general",
        tone=tone or channel.profile.music_mood or "neutral",
        topics=listed,
    )

    client = genai.Client(api_key=key)
    response = client.models.generate_content(model=model, contents=prompt)
    data = _json_from(getattr(response, "text", "") or "")
    if not data:
        logger.warning("Could not parse a brief out of the model response.")
        return None

    if not data.get("suitable", True):
        logger.info(f"Model declined every trending topic: {data.get('why', 'no reason given')}")
        return None

    topic = str(data.get("topic", "")).strip()
    # The model is asked to copy a tag from the list; if it invented one, the
    # measured trend numbers below would be attached to something that was
    # never measured.
    match = next((t for t in topics if t["tag"].lower() == topic.lower()), None)
    if not match:
        logger.warning(
            f"Model returned topic {topic!r}, which is not in the trending list. "
            f"Refusing to label an invented topic as measured.")
        return None

    return Brief(
        topic=match["tag"],
        angle=str(data.get("angle", "")).strip(),
        video_prompt=str(data.get("video_prompt", "")).strip(),
        title=str(data.get("title", "")).strip()[:100],
        caption=str(data.get("caption", "")).strip(),
        hook_type=str(data.get("hook_type", "")).strip(),
        negative_prompt=str(data.get("negative_prompt", "")).strip(),
        trend_videos=match["videos"],
        trend_median_views=match["median_views"],
        rationale=str(data.get("rationale", "")).strip(),
    )
