# agent/audience.py
"""
⚡ NornPulse: Post-generation audience reaction (audience.py)
Norn Labs (nornlabs.ai)

Watches the finished clip and answers "would I keep watching", not "is
this well made" — a different question from `critic.py`'s, and the one a
Shorts channel actually lives or dies on. The critic reads a brief before
anything exists; this reads the artefact itself, in the order a real
viewer would see it: frames and captions, interleaved by time, not the
prompt that produced them.

Why this needs to see the artefact rather than the brief: a brief can read
perfectly and still render with a caption a beat late, a cut that lands on
a held pose the words did not describe, or a title banner that clips at
the edge of a 9:16 frame. None of that is visible in text. It is the same
reasoning `critic.py`'s docstring gives for why it would not have caught
"not funny at all" — the artefact is where a different set of defects live,
and only watching it finds them.

The honest ceiling, same shape as critic.py's: a model watching a model's
own footage shares its blind spots, and six evenly-spaced frames are a
storyboard, not a video — real motion, real audio timing, and anything
that happens strictly between two sampled frames are invisible to it. This
is a cheap, sampled proxy for "would a human scroll here", not a
replacement for a human actually watching. Advisory only: it is shown to
a reviewer alongside the clip, never used to auto-reject anything, because
its ceiling is exactly the kind of thing that should stay visible rather
than get to act on its own judgement.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MODEL = "gemini-3.6-flash"

DEFAULT_FRAMES = 6

# Strips ASS override tags ({\t(...)...}, {\c&HFF41D2&}, {\c}, ...) so the
# model reads the words a viewer actually sees, not the animation script.
_ASS_OVERRIDE = re.compile(r"\{[^}]*\}")


@dataclass
class Reaction:
    """
    Where a viewer would have scrolled, and why — or that they would not
    have, which is the good outcome and is reported as plainly as the bad
    one rather than as silence.
    """

    would_finish: bool
    scroll_point: str = ""          # e.g. "around the 3rd caption" — empty if would_finish
    reasons: List[str] = field(default_factory=list)
    frames_sampled: int = 0
    captions_sampled: int = 0
    checked_by: str = MODEL
    raw: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        if self.would_finish:
            return "would watch to the end"
        return f"would scroll {self.scroll_point}" if self.scroll_point else "would scroll away"


def _probe_duration(video_path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True, timeout=30)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def extract_frames(video_path: Path | str, n: int = DEFAULT_FRAMES) -> List[bytes]:
    """
    n evenly-spaced JPEG frames across the clip, in playback order.

    Spread across the whole duration rather than clustered near the start:
    the hook decides whether a viewer stays, but a held-pose ending or a
    late caption desync — both real past rejections — only shows up later
    in the clip, and a critique that only ever saw the first second would
    never catch either.
    """
    video_path = Path(video_path)
    duration = _probe_duration(video_path)
    if not duration or n < 1:
        return []

    frames: List[bytes] = []
    # Inset from both ends: t=0.0 and t=duration are frequently a black
    # frame or a not-yet-composited edge, which is a sampling artefact, not
    # a finding about the clip.
    inset = min(0.15, duration / (2 * n))
    for i in range(n):
        t = inset + (duration - 2 * inset) * (i / max(n - 1, 1))
        result = subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(video_path),
             "-frames:v", "1", "-q:v", "3", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
            capture_output=True, timeout=30)
        if result.returncode == 0 and result.stdout:
            frames.append(result.stdout)
        else:
            logger.warning(f"Could not extract frame at {t:.2f}s from {video_path}")
    return frames


def read_captions(subs_path: Path | str) -> List[Dict[str, Any]]:
    """
    Ordered (start_sec, text) pairs from a Skuld-rendered .ass sidecar, with
    animation override tags stripped so this reads the words a viewer sees
    rather than the kinetic-caption script that draws them.

    Returns an empty list rather than raising when the sidecar is missing
    or unparseable — a clip can be watched on frames alone, just without
    the caption half of what a viewer actually experiences.
    """
    path = Path(subs_path)
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        logger.warning(f"Could not read subtitle sidecar {path}: {e}")
        return []

    out: List[Dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        # Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV,
        # Effect, Text — Text is free-form and may itself contain commas, so
        # split on the first 9 only.
        parts = line[len("Dialogue:"):].split(",", 9)
        if len(parts) < 10:
            continue
        start_str, raw_text = parts[1].strip(), parts[9]
        clean = _ASS_OVERRIDE.sub("", raw_text).strip()
        if not clean:
            continue
        h, m, s = start_str.split(":")
        start_sec = int(h) * 3600 + int(m) * 60 + float(s)
        out.append({"start_sec": start_sec, "text": clean})
    return out


_PROMPT = """You are watching a short-form vertical video the way a real \
viewer would on a phone feed — not judging whether it was made \
competently, but whether YOU would keep watching or scroll past, and at \
exactly which point.

You are given {n_frames} frames sampled evenly across the clip, in \
playback order, and the on-screen captions with their timing. Watch them \
as a sequence, the way a video actually plays, not as separate images.

CAPTIONS, IN ORDER
{captions}

Answer honestly. If nothing here would make you scroll, say so — a clean \
pass is a real, useful answer, not a failure to find something wrong. If \
something would make you scroll, name the SPECIFIC point (tied to a frame \
number or a caption's wording) and the SPECIFIC reason, not a generic \
note like "pacing could be better".

Return ONLY JSON:
{{"would_finish": true|false,
  "scroll_point": "<specific point you'd scroll at, tied to a frame/caption, or empty if would_finish>",
  "reasons": ["<specific, concrete reason>"]}}"""


def watch(video_path: Path | str, subs_path: Optional[Path | str] = None,
         n_frames: int = DEFAULT_FRAMES, api_key: Optional[str] = None,
         model: str = MODEL) -> Reaction:
    """
    Sample the finished clip and react to it the way a scrolling viewer
    would — the complement to critic.check_brief, applied to the artefact
    instead of the plan for it.
    """
    video_path = Path(video_path)
    frames = extract_frames(video_path, n=n_frames)
    captions = read_captions(subs_path) if subs_path else []

    if not frames:
        return Reaction(would_finish=True, checked_by="none",
                        reasons=["could not extract any frames from this video"])

    captions_block = ("\n".join(f"  {c['start_sec']:.1f}s: {c['text']}" for c in captions)
                      if captions else "  (no caption sidecar given)")

    from agent import genai_client as gc
    from google.genai import types

    key = api_key or os.getenv("GEMINI_API_KEY")
    if not gc.use_vertex() and not key:
        return Reaction(would_finish=True, checked_by="none",
                        frames_sampled=len(frames), captions_sampled=len(captions),
                        reasons=["audience reaction could not run: GEMINI_API_KEY is not set"])

    parts = [types.Part.from_text(text=_PROMPT.format(
        n_frames=len(frames), captions=captions_block))]
    for frame_bytes in frames:
        parts.append(types.Part.from_bytes(data=frame_bytes, mime_type="image/jpeg"))

    try:
        client, model = gc.client_for(model, api_key=key)
        response = client.models.generate_content(
            model=model, contents=[types.Content(role="user", parts=parts)])
        data = _json_from(getattr(response, "text", "") or "")
    except Exception as e:
        logger.warning(f"Audience reaction failed to run: {e}")
        return Reaction(would_finish=True, checked_by="none",
                        frames_sampled=len(frames), captions_sampled=len(captions),
                        reasons=[f"audience reaction could not run: {str(e)[:120]}"])

    if not data:
        return Reaction(would_finish=True, checked_by="none",
                        frames_sampled=len(frames), captions_sampled=len(captions),
                        reasons=["audience reaction returned nothing readable"])

    return Reaction(
        would_finish=bool(data.get("would_finish", True)),
        scroll_point=str(data.get("scroll_point", "")).strip(),
        reasons=[str(r) for r in (data.get("reasons") or [])],
        frames_sampled=len(frames),
        captions_sampled=len(captions),
        raw=data,
    )


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


def describe(reaction: Reaction) -> str:
    """A short block for a terminal or an email, ceiling included."""
    icon = "🟢" if reaction.would_finish else "🟡"
    lines = [f"{icon} audience reaction: {reaction.summary()}"]
    for reason in reaction.reasons:
        lines.append(f"     · {reason}")
    lines.append(f"     sampled {reaction.frames_sampled} frame(s), "
                 f"{reaction.captions_sampled} caption(s) — a storyboard proxy, "
                 f"not a substitute for actually watching it")
    return "\n".join(lines)
