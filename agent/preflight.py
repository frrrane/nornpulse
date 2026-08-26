# agent/preflight.py
"""
⚡ NornPulse: Check a clip against the faults a human has actually rejected (preflight.py)
Norn Labs (nornlabs.ai)

The obvious version of this is a critic agent that watches a clip and says
whether it is any good. The review history says that is not the bottleneck.

Every rejection recorded in this project so far is a mechanical craft
defect, not a judgement about the material:

    "the subtitles arent synced"
    "the start is cut off"
    "cuts of mid sentence"
    "too bouncy"

None of them is "this is boring". They are all things a check can find
before a human spends attention on them, and three of the four are
findable without a model at all. So this module is a checklist derived
from that history rather than an opinion, and it grows only when a new
rejection reason appears that it would have missed.

What it deliberately does not do
--------------------------------
It does not score a clip, rank it, or predict how it will perform. A number
here would be a model judgement wearing the costume of a measurement, and
the whole project rests on not doing that. It answers one question: does
this clip carry a fault that has already caused a rejection?

Taste is left to the human, which is where the review history says it
already lives.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# A sentence that ends within this many seconds of the cut counts as landing
# on the boundary. Transcript timestamps are themselves approximate, so a
# tighter threshold reports noise as a defect.
BOUNDARY_TOLERANCE_SEC = 0.4

_LINE_RE = re.compile(r"\[(\d{1,3}):(\d{2}(?:\.\d+)?)\]\s*(.*)")


@dataclass
class Finding:
    """One fault, named after the rejection that put it on the list."""

    check: str
    detail: str
    # The reviewer comment this check exists because of. Kept so a finding
    # can always answer "who says this matters", rather than asserting a
    # rule nobody agreed to.
    because: str = ""

    def __str__(self) -> str:
        tail = f"  (rejected before as: {self.because!r})" if self.because else ""
        return f"{self.check}: {self.detail}{tail}"


@dataclass
class Report:
    clip_id: str
    findings: List[Finding] = field(default_factory=list)
    checked: List[str] = field(default_factory=list)
    not_checked: List[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.findings

    def describe(self) -> str:
        head = (f"🟢 preflight {self.clip_id}: clean"
                if self.clean else
                f"🔴 preflight {self.clip_id}: {len(self.findings)} fault(s)")
        lines = [head]
        lines += [f"     • {f}" for f in self.findings]
        if self.checked:
            lines.append(f"     checked: {', '.join(self.checked)}")
        if self.not_checked:
            lines.append(f"     NOT checked: {', '.join(self.not_checked)}")
        return "\n".join(lines)


def transcript_lines(transcript_text: str) -> List[Tuple[float, str]]:
    """(absolute seconds, text) for every timestamped line."""
    out = []
    for raw in (transcript_text or "").splitlines():
        m = _LINE_RE.match(raw.strip())
        if m:
            out.append((int(m.group(1)) * 60 + float(m.group(2)), m.group(3).strip()))
    return sorted(out)


def _parse_mmss(value: str) -> Optional[float]:
    try:
        parts = str(value).replace(",", ".").split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (ValueError, AttributeError):
        return None


def check_cut_boundaries(clip: Dict[str, Any], lines: List[Tuple[float, str]]) -> List[Finding]:
    """
    Does the clip open and close on a sentence, or halfway through one?

    Two separate rejections came from this — "the start is cut off" and
    "cuts of mid sentence" — and they are opposite ends of the same
    question. A cut that lands mid-sentence is audible immediately and is
    the first thing a reviewer notices.
    """
    start = _parse_mmss(clip.get("start_time", ""))
    end = _parse_mmss(clip.get("end_time", ""))
    if start is None or end is None or not lines:
        return []

    findings = []
    starts = [t for t, _ in lines]

    opening = min((t for t in starts if t >= start - BOUNDARY_TOLERANCE_SEC), default=None)
    if opening is None or opening - start > BOUNDARY_TOLERANCE_SEC:
        prior = max((t for t in starts if t <= start), default=None)
        if prior is not None:
            findings.append(Finding(
                "start_mid_sentence",
                f"opens {start - prior:.2f}s into a line that began at "
                f"{prior / 60:.0f}:{prior % 60:04.1f}",
                because="the start is cut off"))

    # The last line to begin before the cut ends: if the NEXT line begins
    # after the cut, that last sentence is still being spoken when the clip
    # stops.
    last = max((t for t in starts if t < end), default=None)
    following = min((t for t in starts if t > (last if last is not None else end)),
                    default=None)
    if last is not None and following is not None and following > end + BOUNDARY_TOLERANCE_SEC:
        text = next((x for t, x in lines if t == last), "")
        findings.append(Finding(
            "end_mid_sentence",
            f"stops {following - end:.2f}s before the closing line finishes: {text[:44]!r}",
            because="cuts of mid sentence"))
    return findings


def check_duration(clip: Dict[str, Any], min_sec: float, max_sec: float) -> List[Finding]:
    """
    Is the clip inside the length that has actually travelled here?

    Not a rejection reason anyone wrote down, but the measured difference
    between a median of 343 views and a median of 13, so it is worth
    saying before a human looks rather than after.
    """
    start = _parse_mmss(clip.get("start_time", ""))
    end = _parse_mmss(clip.get("end_time", ""))
    if start is None or end is None:
        return []
    length = end - start
    if length > max_sec:
        return [Finding("too_long",
                        f"{length:.1f}s — every Short published on these "
                        f"channels is {min_sec:.0f}-{max_sec:.0f}s")]
    if length < min_sec:
        return [Finding("too_short", f"{length:.1f}s, under the {min_sec:.0f}s floor")]
    return []


def check_profile(clip: Dict[str, Any], profile: Any) -> List[Finding]:
    """
    Does the clip use a treatment this channel has ruled out?

    "too bouncy" is in the record because a NASA explainer came back
    shaking. The channel profile encodes that decision; this checks the
    render actually honoured it, because a setting that is passed and
    ignored looks identical to one that was never set.
    """
    if not profile:
        return []
    findings = []
    for field_name, key in (("avoid_motion", "motion_effect"),
                            ("avoid_crop", "crop_mode")):
        banned = list(getattr(profile, field_name, []) or [])
        used = clip.get(key)
        if used and used in banned:
            findings.append(Finding(
                f"forbidden_{key}",
                f"{used!r} is in this channel's {field_name}",
                because="too bouncy" if key == "motion_effect" else ""))
    return findings


def check_clip(clip: Dict[str, Any], transcript_text: str = "",
               profile: Any = None, min_sec: float = 6.0,
               max_sec: float = 10.0) -> Report:
    """
    Run every check that does not need the rendered file or a model call.

    Caption/audio agreement is deliberately absent: it needs the render and
    a transcription call, and lives in scripts/verify_captions.py. What is
    NOT checked is reported alongside what is, so a clean report is never
    mistaken for a broader guarantee than it gives.
    """
    lines = transcript_lines(transcript_text)
    report = Report(clip_id=str(clip.get("clip_id", "?")))

    if lines:
        report.findings += check_cut_boundaries(clip, lines)
        report.checked.append("cut lands on sentence boundaries")
    else:
        report.not_checked.append("sentence boundaries (no transcript given)")

    report.findings += check_duration(clip, min_sec, max_sec)
    report.checked.append("duration against what has travelled")

    if profile:
        report.findings += check_profile(clip, profile)
        report.checked.append("channel's forbidden crops and motions")
    else:
        report.not_checked.append("channel profile (none given)")

    report.not_checked.append(
        "whether the captions match the audio — see scripts/verify_captions.py")
    report.not_checked.append("whether the clip is interesting, which is the human's call")
    return report


def check_sidecar(path: str | Path, transcript_text: str = "",
                  profile: Any = None, **kwargs) -> Report:
    """Convenience: run the checks against a written `_metadata.json`."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return check_clip(data, transcript_text=transcript_text, profile=profile, **kwargs)
