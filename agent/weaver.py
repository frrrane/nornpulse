# agent/weaver.py
"""
⚡ NornPulse: Weaving generated footage into a cut clip (weaver.py)
Norn Labs (nornlabs.ai)

The two halves of this project have never touched. `agent.footage`
generates video from a prompt; `agent.skuld_renderer` cuts and composites
video someone else shot. A clip is either wholly borrowed or wholly
invented, and nothing joins them.

Joining them is worth doing twice over.

**The first second.** A Short is decided before a viewer has heard a
sentence, and a cut clip opens on whatever the source happened to be
showing at that moment — a presenter mid-gesture, a slow establishing
shot, a title card. A generated opener is a shot chosen for that job.

**The rights.** Every second of borrowed footage is a second of someone
else's work in the upload. Generated footage is not — so the more of a clip
that is generated, the smaller the surface for a claim. That does not make
the borrowed part safe, and this module cannot make a copyright judgement;
it only changes the ratio, which is a real thing to change.

Two forms
---------
**The opener.** A generated establishing shot in front of the cut,
crossfaded rather than hard-cut — a Short is decided before a viewer has
heard a sentence, and a cut clip opens on whatever the source happened to
be showing at that moment.

**The cutaway.** A generated shot spliced into the MIDDLE of a clip,
picture only — the clip's own audio (source sound, narration, score)
keeps playing underneath, untouched. This is the harder one: it needs a
model to decide WHERE, by actually reasoning about the transcript rather
than the caller nominating a spot, and to correctly decide "nowhere" on
most clips — a 6-9 second Short is usually concretely visual throughout,
and forcing a cutaway onto one that doesn't need it would make the
insert the tic, not the choice. `identify_broll_moment` is that
judgement call; `add_generated_broll` is None on it far more often than
not, and that is the expected, correct outcome, not a degraded one.

What is deliberately not here
-----------------------------
Generated backdrop instead of blur — compositing the source over a
themed generated background rather than a blurred copy of itself. Still
in the backlog, unbuilt.

Cost
----
Every insert is a paid Veo call attached to a clip that previously cost
nothing beyond ffmpeg. At six uploads a day that is a real line item, so
nothing here happens implicitly: a caller has to ask. The cutaway spends
twice over on a clip that turns out not to need one — once to reason
about it, once (if a moment was found) to generate it — though the
reasoning call is a cheap text call, not the Veo generation itself.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("nornpulse.weaver")

# Veo bills per second of output and will not produce arbitrarily short
# clips, so an opener is generated at the model's own length and trimmed.
# Paying for eight seconds to keep two is the price of the shortest thing
# the generator will make.
GENERATE_SEC = 8

# How much generated footage goes in front. Long enough to register as a
# shot, short enough that the borrowed material still starts inside the
# window a viewer decides in.
DEFAULT_OPENER_SEC = 2.0
MAX_OPENER_SEC = 4.0

# Everything is normalised to this before concatenation. Veo's fast model
# returns 720x1280; Skuld renders 1080x1920. Concatenating streams that
# disagree about size produces a file that plays for exactly as long as its
# first segment and then stops.
TARGET_W, TARGET_H = 1080, 1920
TARGET_FPS = 30
TARGET_SAMPLE_RATE = 44100

# How long the two shots overlap. A hard cut from generated footage to
# borrowed footage announces the seam -- a reviewer described it as cutting
# "unexpectedly at the second second" -- because nothing in the opener
# prepares the eye for it. A short dissolve reads as one piece of editing
# instead of two clips glued together.
#
# Kept brief: long enough to soften the join, short enough that it does not
# eat the opener, which is only a couple of seconds to begin with.
CROSSFADE_SEC = 0.5

MODEL = "gemini-3.6-flash"

# B-roll span length, in seconds. Long enough to register as a cutaway,
# short enough that it stays a moment within the clip rather than taking it
# over -- a 6-9 second Short losing a third of itself to a single inserted
# shot would read as the insert being the point, not the narration.
MIN_BROLL_SEC = 1.0
MAX_BROLL_SEC = 3.0


class WeaveError(RuntimeError):
    """Raised when the pieces exist but could not be joined."""


@dataclass
class Woven:
    """The result, and what it actually cost."""

    path: Path
    opener_sec: float = 0.0
    broll_sec: float = 0.0
    generated: bool = False
    prompt: str = ""
    note: str = ""


def opener_prompt(hook_title: str, topic: str = "") -> str:
    """
    A prompt for the shot that goes in front.

    Written to describe an establishing image rather than a story: it has
    two seconds, no dialogue, and its only job is to make the borrowed
    footage behind it look intentional. Asking for a narrative in that
    space produces a rushed one.
    """
    subject = (topic or hook_title or "the subject").strip()
    return (
        f"Cinematic establishing shot introducing: {subject}. "
        f"Vertical 9:16. One continuous slow camera move, no cuts. "
        f"No text, no captions, no titles, no logos, no readable writing "
        f"anywhere in frame. No people speaking to camera. "
        f"Dramatic, high-contrast lighting suited to a documentary opening."
    )


def _has_audio(path: Path) -> bool:
    if not shutil.which("ffprobe"):
        return False
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60).stdout
        return bool(out.strip())
    except Exception:
        return False


def _normalise(label: str, has_audio: bool, trim_to: Optional[float] = None):
    """
    Filter fragments bringing one input to the common format.

    Scaled to cover and then cropped rather than padded: an opener with
    black bars against a full-frame clip reads as a rendering fault at the
    join, which is the one place a viewer is definitely looking.
    """
    video = (f"[{label}:v]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
             f"crop={TARGET_W}:{TARGET_H},setsar=1,fps={TARGET_FPS}")
    if trim_to:
        video += f",trim=duration={trim_to:.2f},setpts=PTS-STARTPTS"
    video += f"[v{label}]"

    if has_audio:
        audio = (f"[{label}:a]aformat=sample_rates={TARGET_SAMPLE_RATE}:"
                 f"channel_layouts=stereo")
        if trim_to:
            audio += f",atrim=duration={trim_to:.2f},asetpts=PTS-STARTPTS"
        audio += f"[a{label}]"
    else:
        # A silent track rather than no track. concat refuses a mix of
        # inputs with and without audio, and the failure is a filter-graph
        # error rather than a silent segment.
        duration = trim_to or GENERATE_SEC
        audio = (f"anullsrc=r={TARGET_SAMPLE_RATE}:cl=stereo:d={duration:.2f}"
                 f"[a{label}]")
    return video, audio


def weave_opener(clip_path: str | Path, opener_path: str | Path,
                 out_path: str | Path,
                 opener_sec: float = DEFAULT_OPENER_SEC) -> Woven:
    """
    Put `opener_path` in front of `clip_path`, trimmed and matched.

    Raises rather than degrading. Unlike the finishing pass — where losing
    narration still leaves a publishable clip — a half-joined video is not
    a lesser version of the result, and writing one would be worse than
    keeping the clip that already worked.
    """
    clip_path, opener_path, out_path = Path(clip_path), Path(opener_path), Path(out_path)
    for required in (clip_path, opener_path):
        if not required.exists() or required.stat().st_size == 0:
            raise WeaveError(f"Nothing to weave: {required} is missing or empty.")

    opener_sec = max(0.5, min(float(opener_sec), MAX_OPENER_SEC))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # The dissolve cannot be longer than the shot it is dissolving out of,
    # and needs some opener left over on either side of it.
    fade = min(CROSSFADE_SEC, max(0.0, opener_sec - 0.5))

    opener_video, opener_audio = _normalise("0", _has_audio(opener_path), opener_sec)
    clip_video, clip_audio = _normalise("1", _has_audio(clip_path))

    if fade > 0:
        # xfade starts the transition at `offset` into the first input, so
        # the two overlap rather than abut. Total length is therefore
        # opener + clip - fade, not opener + clip.
        join = (f"[v0][v1]xfade=transition=fade:duration={fade:.2f}"
                f":offset={opener_sec - fade:.2f}[v];"
                f"[a0][a1]acrossfade=d={fade:.2f}[a]")
    else:
        join = "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"

    graph = ";".join([opener_video, opener_audio, clip_video, clip_audio, join])

    cmd = [
        "ffmpeg", "-v", "error",
        "-i", str(opener_path), "-i", str(clip_path),
        "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart",
        "-y", str(out_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    except subprocess.CalledProcessError as e:
        raise WeaveError(
            f"ffmpeg could not join the two: "
            f"{e.stderr.decode('utf-8', 'replace')[:300]}") from e
    except Exception as e:
        raise WeaveError(f"ffmpeg could not join the two: {e}") from e

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise WeaveError(f"The join reported success but {out_path} is empty.")

    return Woven(path=out_path, opener_sec=opener_sec, generated=True)


def add_generated_opener(clip_path: str | Path, hook_title: str,
                         clip_id: str, topic: str = "",
                         opener_sec: float = DEFAULT_OPENER_SEC,
                         out_path: Optional[str | Path] = None,
                         check_rights: bool = True,
                         **veo_kwargs) -> Woven:
    """
    Generate an opening shot and put it in front of an existing clip.

    The rights check runs on the prompt before anything is generated, for
    the same reason it does in the trend loop: a refusal discovered after
    the call has already been paid for is a refusal discovered too late.
    """
    from agent import footage as fg

    clip_path = Path(clip_path)
    out_path = Path(out_path) if out_path else clip_path.with_name(
        f"{clip_path.stem}_woven.mp4")
    prompt = opener_prompt(hook_title, topic)

    if check_rights:
        from agent import watchdog as wd
        verdict = wd.check_text(title=hook_title, prompt=prompt)
        if verdict.blocked:
            raise WeaveError(
                f"Rights check blocked the opener before generating it: "
                f"{verdict.summary()}")
        if verdict.needs_human:
            logger.warning(
                f"Opener flagged for a human look: {verdict.summary()}")

    generated_path = clip_path.with_name(f"{clip_id}_opener.mp4")
    shot = fg.generate_with_veo(
        prompt=prompt, out_path=generated_path,
        duration_sec=GENERATE_SEC, aspect_ratio="9:16", **veo_kwargs)

    woven = weave_opener(clip_path, shot.path, out_path, opener_sec=opener_sec)
    woven.prompt = prompt
    woven.note = (f"{opener_sec:.1f}s generated opener from a {GENERATE_SEC}s "
                  f"generation — Veo will not make anything shorter")
    return woven


# ---------------------------------------------------------------------------
# Generated B-roll under narration
# ---------------------------------------------------------------------------
# The piece the module docstring used to point at as unbuilt. Not a wiring
# job like the opener: an establishing shot suits any clip, but a cutaway
# only earns its place where the words are describing something the
# footage genuinely cannot show, and finding that moment is a judgement
# call, not a lookup. So this asks a model to make it, and accepts "no
# moment in this clip needs one" as the common, correct answer -- forcing
# an insert onto every clip would make the insert the tic, not the choice.

_BROLL_PROMPT = """You are deciding whether ONE moment in this short video \
clip's narration would benefit from a generated cutaway shot.

A cutaway earns its place when the narration is describing something the \
camera physically cannot show where it's pointed — an abstract process, a \
distant or long-ago event, something happening at a scale or in a place no \
lens reaches. It does NOT earn its place when the narration describes \
something concrete that ordinary footage of the subject already shows \
fine — a person doing something, an object, a place the camera can just \
point at.

NARRATION IN THIS CLIP (seconds from the start of the clip, in order):
{cues}

Pick AT MOST ONE contiguous span of consecutive lines above where a \
cutaway would genuinely help. If nothing in this clip clearly needs one — \
including if the whole clip is already concretely visual — say so; that is \
the common, correct answer, not a failure to find something.

The span must be between {min_sec:.0f} and {max_sec:.0f} seconds long, and \
its start/end must be timestamps that actually appear above.

Return ONLY JSON:
{{"has_moment": true|false,
  "start_sec": <number, only if has_moment>,
  "end_sec": <number, only if has_moment>,
  "visual_concept": "<what to actually show, concrete and filmable, only if has_moment>",
  "reason": "<why this moment specifically, or why nothing qualifies>"}}"""


def _json_from(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        start, end = text.find("{"), text.rfind("}")
        raw = text[start:end + 1] if start != -1 and end > start else None
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None


def clip_relative_cues(transcript_cues: List[Tuple[float, str]],
                       clip_start_sec: float, clip_end_sec: float) -> List[Tuple[float, str]]:
    """
    Cues within [clip_start_sec, clip_end_sec], retimed to seconds from the
    CLIP's own start rather than the source video's — the frame the
    reasoning prompt and the eventual splice both need to reason in,
    since the rendered clip's own timeline starts at 0.
    """
    return [
        (round(at - clip_start_sec, 2), text)
        for at, text in transcript_cues
        if clip_start_sec - 0.5 <= at <= clip_end_sec and text.strip()
    ]


def identify_broll_moment(transcript_cues: List[Tuple[float, str]],
                          clip_start_sec: float, clip_end_sec: float,
                          api_key: Optional[str] = None, model: str = MODEL
                          ) -> Optional[Dict[str, Any]]:
    """
    Real reasoning over this clip's own narration: is there a moment that
    needs a cutaway, and if so, exactly where and to what.

    Returns None both when nothing qualifies and when the check could not
    run at all (no key, an API error, an unparseable answer) — unlike
    critic.py and watchdog.py, there is no unsafe direction to fail
    toward here: skipping a cutaway on a clip that might have benefited
    from one costs nothing but the missed upside, so this fails toward
    "do not spend", not toward "ask a human".
    """
    cues = clip_relative_cues(transcript_cues, clip_start_sec, clip_end_sec)
    if not cues:
        return None
    cues_block = "\n".join(f"  {at:.1f}s: {text}" for at, text in cues)

    from agent import genai_client as gc

    key = api_key or os.getenv("GEMINI_API_KEY")
    if not gc.use_vertex() and not key:
        logger.info("B-roll reasoning could not run: GEMINI_API_KEY is not set.")
        return None

    try:
        client, model = gc.client_for(model, api_key=key)
        response = client.models.generate_content(
            model=model,
            contents=_BROLL_PROMPT.format(
                cues=cues_block, min_sec=MIN_BROLL_SEC, max_sec=MAX_BROLL_SEC),
        )
        data = _json_from(getattr(response, "text", "") or "")
    except Exception as e:
        logger.info(f"B-roll reasoning could not run: {e}")
        return None

    if not data or not data.get("has_moment"):
        return None

    try:
        start_sec = float(data["start_sec"])
        end_sec = float(data["end_sec"])
    except (KeyError, TypeError, ValueError):
        logger.warning("B-roll reasoning claimed a moment but gave no usable timing.")
        return None

    span = end_sec - start_sec
    if span <= 0 or start_sec < 0 or end_sec > (clip_end_sec - clip_start_sec):
        logger.warning(
            f"B-roll reasoning returned a span outside the clip "
            f"({start_sec:.1f}-{end_sec:.1f}s); discarding it.")
        return None
    # Clamp to the configured bounds rather than discard: a model landing
    # just outside a soft limit is a rounding disagreement, not a reason
    # to throw away an otherwise-good pick.
    span = max(MIN_BROLL_SEC, min(span, MAX_BROLL_SEC))
    end_sec = start_sec + span

    return {
        "start_sec": start_sec,
        "end_sec": end_sec,
        "visual_concept": str(data.get("visual_concept", "")).strip(),
        "reason": str(data.get("reason", "")).strip(),
    }


def broll_prompt(visual_concept: str) -> str:
    """
    A prompt for the cutaway itself. Same constraints as the opener's — no
    text, no people speaking to camera, a single continuous shot — since
    this too is a wordless establishing image, just placed mid-clip
    instead of in front of it.
    """
    subject = (visual_concept or "the subject").strip()
    return (
        f"Cinematic establishing shot depicting: {subject}. "
        f"Vertical 9:16. One continuous slow camera move, no cuts. "
        f"No text, no captions, no titles, no logos, no readable writing "
        f"anywhere in frame. No people speaking to camera. "
        f"Dramatic, high-contrast lighting suited to a documentary."
    )


def insert_broll(clip_path: str | Path, broll_path: str | Path,
                 start_sec: float, end_sec: float,
                 out_path: str | Path) -> Woven:
    """
    Replace clip_path's picture between start_sec and end_sec with
    broll_path's, keeping clip_path's audio completely untouched.

    This is a video-only swap, not a join: the clip's own soundtrack
    (source audio, narration, score — whatever Skuld already mixed) plays
    through the cutaway exactly as it would have through the footage it
    replaced. Total duration is therefore unchanged, so the audio needs
    no retiming — mapped and stream-copied straight from the input,
    never re-encoded, which is also the only way to guarantee it stays
    bit-identical to what Skuld actually rendered.
    """
    clip_path, broll_path, out_path = Path(clip_path), Path(broll_path), Path(out_path)
    for required in (clip_path, broll_path):
        if not required.exists() or required.stat().st_size == 0:
            raise WeaveError(f"Nothing to weave: {required} is missing or empty.")

    from agent.skuld_renderer import get_video_duration_seconds
    total = get_video_duration_seconds(clip_path)
    if not (0 <= start_sec < end_sec <= total):
        raise WeaveError(
            f"B-roll span {start_sec:.1f}-{end_sec:.1f}s does not fit inside "
            f"a {total:.1f}s clip.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    span = end_sec - start_sec

    # setsar=1 on every segment, not just the B-roll: the base clip's own
    # encoding carries whatever SAR its render pipeline left it with, and
    # concat refuses to join segments whose SAR disagrees even when their
    # pixel dimensions match (confirmed live).
    filters = [f"[0:v]trim=start=0:end={start_sec:.3f},setpts=PTS-STARTPTS,setsar=1[vbefore]"]
    if end_sec < total:
        filters.append(f"[0:v]trim=start={end_sec:.3f}:end={total:.3f},"
                       f"setpts=PTS-STARTPTS,setsar=1[vafter]")
    filters.append(
        f"[1:v]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_W}:{TARGET_H},setsar=1,fps={TARGET_FPS},"
        f"trim=duration={span:.3f},setpts=PTS-STARTPTS[vbroll]")
    concat_inputs = "[vbefore][vbroll]" + ("[vafter]" if end_sec < total else "")
    n = 3 if end_sec < total else 2
    filters.append(f"{concat_inputs}concat=n={n}:v=1:a=0[v]")
    graph = ";".join(filters)

    has_audio = _has_audio(clip_path)
    cmd = ["ffmpeg", "-v", "error", "-i", str(clip_path), "-i", str(broll_path),
           "-filter_complex", graph, "-map", "[v]"]
    if has_audio:
        cmd += ["-map", "0:a", "-c:a", "copy"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-y", str(out_path)]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    except subprocess.CalledProcessError as e:
        raise WeaveError(
            f"ffmpeg could not insert the cutaway: "
            f"{e.stderr.decode('utf-8', 'replace')[:300]}") from e
    except Exception as e:
        raise WeaveError(f"ffmpeg could not insert the cutaway: {e}") from e

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise WeaveError(f"The insert reported success but {out_path} is empty.")

    return Woven(path=out_path, broll_sec=span, generated=True)


def add_generated_broll(clip_path: str | Path, clip_id: str,
                        transcript_cues: List[Tuple[float, str]],
                        clip_start_sec: float, clip_end_sec: float,
                        out_path: Optional[str | Path] = None,
                        check_rights: bool = True,
                        **veo_kwargs) -> Woven:
    """
    Find a moment in this clip that needs a cutaway, generate it, and
    splice it in — or hand the clip back unchanged if no moment qualifies.

    Unlike add_generated_opener, returning the clip unchanged (generated=
    False) is the expected outcome on most calls, not a degraded one: most
    6-9 second clips are concretely visual throughout, and inserting a
    cutaway into one of those would be the tic the reasoning step exists
    to avoid.
    """
    clip_path = Path(clip_path)
    out_path = Path(out_path) if out_path else clip_path.with_name(
        f"{clip_path.stem}_broll.mp4")

    moment = identify_broll_moment(transcript_cues, clip_start_sec, clip_end_sec)
    if moment is None:
        return Woven(path=clip_path, generated=False,
                     note="no moment in this clip needed a generated cutaway")

    from agent import footage as fg

    prompt = broll_prompt(moment["visual_concept"])

    if check_rights:
        from agent import watchdog as wd
        verdict = wd.check_text(prompt=prompt)
        if verdict.blocked:
            raise WeaveError(
                f"Rights check blocked the cutaway before generating it: "
                f"{verdict.summary()}")
        if verdict.needs_human:
            logger.warning(f"Cutaway flagged for a human look: {verdict.summary()}")

    generated_path = clip_path.with_name(f"{clip_id}_broll_source.mp4")
    shot = fg.generate_with_veo(
        prompt=prompt, out_path=generated_path,
        duration_sec=GENERATE_SEC, aspect_ratio="9:16", **veo_kwargs)

    woven = insert_broll(clip_path, shot.path, moment["start_sec"], moment["end_sec"], out_path)
    woven.prompt = prompt
    woven.note = (f"{woven.broll_sec:.1f}s cutaway at {moment['start_sec']:.1f}s "
                  f"— {moment['reason']}")
    return woven
