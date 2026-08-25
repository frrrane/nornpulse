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

What is deliberately not here
-----------------------------
No attempt to detect *which* moments of a source lack visual support. That
is the version worth having — generated B-roll under narration where the
audio is more interesting than the picture — and it needs the model to
reason about the footage rather than the caller to nominate a spot. It is
in the backlog, unbuilt, and this is the contained piece underneath it.

Cost
----
Every opener is a paid Veo call attached to a clip that previously cost
nothing beyond ffmpeg. At six uploads a day that is a real line item, so
nothing here happens implicitly: a caller has to ask.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

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


class WeaveError(RuntimeError):
    """Raised when the pieces exist but could not be joined."""


@dataclass
class Woven:
    """The result, and what it actually cost."""

    path: Path
    opener_sec: float
    generated: bool
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
