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
from agent.urdr_analytics import hook_title_guidance_block

logger = logging.getLogger(__name__)

MODEL = "gemini-3.6-flash"

# How many trending tags the model is asked to choose between. Enough for a
# real choice, few enough that the prompt stays cheap.
TOPIC_SHORTLIST = 40

# A tag carried by only one or two trending videos is noise rather than a
# trend, and grounding a whole video in it would overstate the evidence.
MIN_TREND_VIDEOS = 2

# How many premises the model writes before one is chosen. Three is enough
# for the second and third to escape the first idea's gravity, and it is one
# call either way — the expensive step is generation, not writing.
CANDIDATES = 3

# Terms that suppress exactly the register a slop-comedy channel trades in.
#
# The model appends these reflexively: every prompt-writing guide it has read
# ends with "avoid: low quality, blurry, artifacts, deformed". On a channel
# whose best performers are titled "Everything's Slightly Off & Deeply
# Unsettling", that list removes the joke. The first two generations for the
# comedy channel came back beautifully shot and completely unfunny — a
# cinematic, golden-hour, shallow-depth-of-field short film about a man
# eating boiled peanuts. The brief was executed faithfully; the brief asked
# for the wrong thing.
#
# Stripped rather than argued with, for the same reason the rights check has
# a pattern net under its model call: telling a model not to add something is
# an instruction, not a control.
# Matched as a vocabulary rather than as phrases: an item is dropped when
# every word in it is one of these. "glitchy artifacts" and "deformed hands"
# are two words each and arrive in a dozen combinations, so enumerating the
# phrases loses to the model's inventiveness. Nothing worth keeping in a
# negative prompt — watermarks, text overlay, logos, montage, black frame —
# shares a single word with this list, so the rule is safe in the direction
# that matters.
#
# Deliberately absent: horror, scary, disturbing, gore. Those are about what
# the video is *about*, not how it looks, and a channel is entitled to avoid
# them.
_POLISH_WORDS = frozenset("""
    low lo res resolution quality bad poor worst awful terrible
    blurry blurred blur grainy grain noisy noise compression compressed
    out of focus
    glitch glitchy glitches glitching artifact artifacts artifacting
    distorted distortion deformed deformity disfigured malformed
    mutated mutation warped
    ugly creepy unsettling uncanny weird cursed nightmarish
    amateur amateurish cheap crude janky sloppy
    jpeg jpg pixelated pixelation pixels aliasing
    extra missing fingers limbs arms legs hands anatomy proportions
""".split())


# Words that, as the last thing a prompt says happens, mean the clip ends on
# a held pose. Veo fills unwritten time by holding the frame, so a brief that
# stops describing action at five seconds produces three seconds of a subject
# sitting still -- which on an eight-second video is most of it. Checked
# rather than merely asked for, because asking did not work.
_HELD_POSE = re.compile(
    r"\b(sits?|sitting|stares?|staring|holds?|holding|remains?|remaining"
    r"|poses?|posing|stands?|standing|waits?|waiting|looks? (?:at|into)"
    r"|gazes?|gazing|freezes?|frozen|motionless|still)\b", re.I)

# Outcome promises a still image cannot keep. A title claiming something
# "backfires" over footage where nothing backfires reads as a broken upload.
_UNSUPPORTED_OUTCOME = re.compile(
    r"\b(backfires?|goes wrong|gone wrong|ends? in|fails?|failure"
    r"|you won'?t believe|wait for it|watch what happens|disaster)\b", re.I)


def brief_warnings(brief) -> List[str]:
    """
    Problems worth seeing before paying to generate this.

    Both of these were rejected by a human reviewer after the video existed,
    which is the expensive way to find them. They are visible in the text.
    """
    warnings: List[str] = []

    prompt = (brief.video_prompt or "").strip()
    # The last sentence is what the generator is left doing.
    tail = [p for p in re.split(r"(?<=[.!?])\s+", prompt) if p.strip()]
    if tail and _HELD_POSE.search(tail[-1]):
        warnings.append(
            "the prompt ends on a held pose, so the last seconds will very "
            "likely be a static frame")

    hit = _UNSUPPORTED_OUTCOME.search(brief.title or "")
    if hit:
        warnings.append(
            f"the title promises an outcome ({hit.group(0)!r}) that eight "
            f"seconds of footage probably will not show")

    return warnings


def is_comedy(channel) -> bool:
    """Whether this channel's humour depends on things looking wrong."""
    from agent.channels import CATEGORY_COMEDY
    if getattr(channel.profile, "category_id", "") == CATEGORY_COMEDY:
        return True
    hints = {h.lower() for h in getattr(channel.profile, "topic_hints", [])}
    return bool(hints & {"funny", "comedy", "humour", "humor", "meme", "memes"})


def _is_polish_guard(item: str) -> bool:
    """Whether a negative-prompt item is purely a complaint about fidelity."""
    words = re.findall(r"[a-z]+", item.replace("-", " ").lower())
    return bool(words) and all(w in _POLISH_WORDS for w in words)


def strip_polish_guards(negative_prompt: str) -> str:
    """
    Drop the terms that would sand the texture off a comedy clip.

    Operates on whole comma-separated items, so "glitchy artifacts" goes and
    "a glitch in the mainframe" — where the guard words sit alongside real
    ones — stays.
    """
    kept = [part.strip() for part in negative_prompt.split(",")
            if part.strip() and not _is_polish_guard(part)]
    return ", ".join(kept)


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
{voice}

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
Two failure modes, and you will default to both unless you fight them.

FAILURE ONE: the cute tableau. A capybara in a wig. A dog in a suit. An \
avocado crying. These are stock images with motion. Every AI video is this. \
They are not jokes, because nothing happens and nothing turns.

FAILURE TWO — and read this twice — POLISHED CORPORATE DEADPAN. A composed \
executive reacting calmly to chaos. A pitchman who will not break character. \
An office worker maintaining dignity. This is the median of everything ever \
written about comedy and it is instantly recognisable as a machine's idea of \
funny. If your premise contains a boardroom, a studio, a suit, or the word \
"unbothered", throw it away and start again.

What actually works on a channel like this:
- **Cursed juxtaposition.** Two things that must never meet, colliding with \
total commitment. Prehistoric plumbing. Opera-singing wildlife. A medieval \
siege fought over a parking space.
- **Mangled naming.** Deliberately wrong, almost-right words are funnier than \
correct ones. Not clever wordplay — wrong in the specific way a bad \
translation is wrong.
- **Escalating literalism.** Take an idiom or a mundane complaint and stage it \
with the budget and gravity of an epic.
- **Anticlimax.** Enormous build-up, pathetically small payoff. The reveal \
should be smaller than promised, not bigger.
- **Texture over polish.** Specific, grubby, regional, over-detailed. Grandeur \
applied to something beneath contempt.

Craft rules that still hold:
- Be SPECIFIC. Detail implies a whole situation; vagueness implies nothing.
- The funniest thing on screen is a BEHAVIOUR, not a costume.

USE ALL EIGHT SECONDS — THREE BEATS, NOT TWO
The last clip failed here. It set something up, revealed the joke at four \
seconds, and then the subject sat still and stared for the remaining three \
and a half. The generator did exactly as told: nothing was written for the \
back half, so nothing happened in it. Dead air at the end of an eight-second \
video is most of the video.

Write the prompt as three timed beats that together fill the full eight \
seconds:
  0-3s  SETUP — establish the situation, already in motion.
  3-5s  TURN — the reveal. What it actually is.
  5-8s  ESCALATION — and this is the one you will forget. Something must \
CONTINUE to happen. The situation gets worse, or more committed, or a \
second smaller thing goes wrong. Someone reacts.

The final beat may NOT be a held pose. If your third beat contains "sits", \
"stares", "holds", "remains", "poses", "looks at the camera" as the last \
thing that happens, it is not a beat and you must write a different one. \
End on an action, mid-motion.

THE TITLE MUST DESCRIBE THIS VIDEO
The last title was "Florida Lawn Care Accordion Spell Backfires" over \
footage of an alligator sitting in a lawn chair. No lawn care, no spell, \
nothing backfiring. A viewer reads the title and sees something unrelated, \
which reads as a broken upload rather than a joke.

Name what is LITERALLY VISIBLE, and lead with the thing from your third \
beat. Do not use "backfires", "goes wrong", "gone wrong", "fails", "ends \
in", "you won't believe" or any construction that promises an outcome the \
footage does not show. If someone watched muted with the title covered, \
they should be able to guess the title.

The IP constraint is real and not negotiable: no copyrighted characters, no \
real identifiable people, no brands. Note that this channel's own back \
catalogue leans on them heavily — you must hit the same ENERGY with original \
subjects. Invent the cursed thing rather than borrowing it.
{look}
WRITE THREE, THEN CHOOSE
Write {n} genuinely DIFFERENT premises before choosing one. Different means \
different topics, or takes so far apart they could not be confused — not \
three rewordings of one idea. Your first idea is almost always the most \
obvious one available, which is why it is written first and why it is rarely \
the funniest.

Then pick. Judge them on which would make a stranger mid-scroll actually \
stop — NOT on which is the most competently written or the most tasteful. \
The best one is usually the one that felt slightly too stupid to submit.

Return ONLY JSON:
{{
  "suitable": true,
  "candidates": [
    {{
      "topic": "<the exact tag, copied from the list above>",
      "angle": "<the comedic or editorial take, one sentence>",
      "video_prompt": "<a vivid, self-contained prompt for a text-to-video \
model, written as the three timed beats above so the full eight seconds are \
filled: setting and look, then 0-3s, 3-5s and 5-8s each saying what HAPPENS. \
The 5-8s beat must be an action, not a held pose. No named characters or \
brands.>",
      "negative_prompt": "<what the generator should avoid, comma separated>",
      "hook_type": "<one of: shock_stat, curiosity_gap, contrarian_claim, \
problem_agitation, direct_question, visual_disruption, metaphor_analogy, \
story_in_medias_res -- decide this BEFORE writing title below, since the \
title has to be written to match it, not labelled after the fact>",
      "title": "<YouTube title under 80 characters, WRITTEN TO MATCH THE \
hook_type CHOSEN ABOVE per the guidance below -- not a plain description of \
the topic with a hook_type label stapled on. Lead with the thing from the \
5-8s beat. No outcome words the footage does not show. End with one or two \
emoji that match what is on screen -- every video that has actually \
travelled on this channel has them, and they are stripped automatically \
from the burned-in banner and the spoken line, so they cost nothing on \
screen.\n{hook_guidance}>",
      "caption": "<one-line description>",
      "rationale": "<why this topic suits THIS channel, one sentence>"
    }}
  ],
  "pick": <0-based index of the one you are choosing>,
  "pick_reason": "<why that one and not the other two, one sentence>"
}}"""


# Appended for comedy channels only. Everything above is about what happens;
# this is about what it looks like, which turned out to be the half that was
# wrong.
_COMEDY_LOOK = """
HOW IT MUST LOOK — this matters as much as the premise
The last two videos this channel generated failed here, not on the writing. \
Both were shot beautifully: golden hour, shallow depth of field, cinematic \
grade, hyper-detailed. Both were completely unfunny. A well-made short film \
about a stupid thing is not a joke about the thing — the craft cancels it. \
The audience for this channel is there for footage that looks WRONG.

So:
- Do NOT ask for: cinematic, filmic, film grain, 8k, hyper-detailed, \
photorealistic, professionally shot, shallow depth of field, golden hour, \
dramatic lighting, colour grading, epic.
- DO ask for the register the channel actually trades in: flat and \
over-lit, garish over-saturated colour, cheap consumer-camera or early-CGI \
look, plasticky surfaces, subjects staring slightly past the lens, motion \
that is a little too smooth or a little too stiff, compositions that are \
almost but not quite centred.
- Leave the negative_prompt for genuine ruin only — a black frame, text and \
watermarks, a montage. Do NOT put low quality, blurry, glitchy, deformed, \
ugly, uncanny or artifacts in it. Those describe the aesthetic, not the \
failure. Anything of that kind will be stripped out before generation \
anyway, so spending the field on it just wastes it.

The target is "somebody rendered this in 2007 and something is deeply off \
about it", not "this could screen at a festival"."""


def write_brief(channel, topics: List[Dict[str, Any]],
                tone: str = "", api_key: Optional[str] = None,
                model: str = MODEL,
                voice: Optional[List[Dict[str, Any]]] = None) -> Optional[Brief]:
    """
    Ask the model to pick a topic and design a video for it.

    Returns None when the model judges that nothing trending suits this
    channel. That is a real answer, not a failure — a comedy channel is not
    obliged to have a take on whatever happens to be popular today, and
    forcing one produces exactly the off-brand filler this loop exists to
    avoid.
    """
    from agent import genai_client as gc

    # On Vertex the credentials are the environment's, not a key.
    key = api_key or os.getenv("GEMINI_API_KEY")
    if not gc.use_vertex() and not key:
        raise RuntimeError("GEMINI_API_KEY is not set.")
    if not topics:
        logger.warning("No trending topics to choose from.")
        return None

    listed = "\n".join(
        f"  - {t['tag']}  ({t['videos']} videos, median {t['median_views']:,.0f} views)"
        for t in topics)

    # The channel's own best-performing titles, as measured voice. Without
    # this the model writes the median of everything it has read, which for
    # a comedy channel is uniformly the wrong register.
    voice_block = ""
    if voice is None:
        try:
            from agent.channel_history import voice_reference
            voice = voice_reference(channel.slug)
        except Exception as e:
            logger.warning(f"No voice reference available: {e}")
            voice = []
    if voice:
        lines = "\n".join(f"    {v['views']:>6,} views · {v['title']}" for v in voice)
        voice_block = (
            "\n  THIS CHANNEL'S OWN VOICE (real titles, best performers first —\n"
            "  match this register, not a generic idea of comedy):\n" + lines)

    comedy = is_comedy(channel)
    prompt = _BRIEF_PROMPT.format(
        title=channel.title,
        hints=", ".join(channel.profile.topic_hints) or "general",
        tone=tone or channel.profile.music_mood or "neutral",
        voice=voice_block,
        topics=listed,
        look=_COMEDY_LOOK if comedy else "",
        n=CANDIDATES,
        hook_guidance=hook_title_guidance_block(),
    )

    client, model = gc.client_for(model, api_key=key)
    response = client.models.generate_content(model=model, contents=prompt)
    data = _json_from(getattr(response, "text", "") or "")
    if not data:
        logger.warning("Could not parse a brief out of the model response.")
        return None

    if not data.get("suitable", True):
        logger.info(f"Model declined every trending topic: {data.get('why', 'no reason given')}")
        return None

    # A model asked for a list will occasionally return a single object. Both
    # shapes are accepted rather than refused, because the content is fine
    # and the only thing lost is the choice between alternatives.
    raw = data.get("candidates")
    candidates = raw if isinstance(raw, list) and raw else [data]

    built = [b for b in (_brief_from(c, topics, comedy) for c in candidates) if b]
    if not built:
        logger.warning("No candidate survived validation against the trending list.")
        return None

    index = data.get("pick", 0)
    try:
        chosen = built[int(index)]
    except (TypeError, ValueError, IndexError):
        logger.warning(f"Model returned an unusable pick {index!r}; taking the first.")
        chosen = built[0]

    chosen.extra["pick_reason"] = str(data.get("pick_reason", "")).strip()
    chosen.extra["alternatives"] = [
        {"title": b.title, "angle": b.angle, "topic": b.topic}
        for b in built if b is not chosen
    ]
    if len(built) > 1:
        logger.info(f"Chose 1 of {len(built)} premises: {chosen.title!r}")
    return chosen


def _brief_from(data: Dict[str, Any], topics: List[Dict[str, Any]],
                comedy: bool) -> Optional[Brief]:
    """
    Build one Brief from one candidate, or None if its topic is not real.

    The model is asked to copy a tag from the list; if it invented one, the
    measured trend numbers would be attached to something that was never
    measured, which is the one thing this pipeline must not do.
    """
    topic = str(data.get("topic", "")).strip()
    match = next((t for t in topics if t["tag"].lower() == topic.lower()), None)
    if not match:
        logger.warning(
            f"Discarding candidate: topic {topic!r} is not in the trending list. "
            f"Refusing to label an invented topic as measured.")
        return None

    negative = str(data.get("negative_prompt", "")).strip()
    if comedy:
        negative = strip_polish_guards(negative)

    return Brief(
        topic=match["tag"],
        angle=str(data.get("angle", "")).strip(),
        video_prompt=str(data.get("video_prompt", "")).strip(),
        title=str(data.get("title", "")).strip()[:100],
        caption=str(data.get("caption", "")).strip(),
        hook_type=str(data.get("hook_type", "")).strip(),
        negative_prompt=negative,
        trend_videos=match["videos"],
        trend_median_views=match["median_views"],
        rationale=str(data.get("rationale", "")).strip(),
    )
