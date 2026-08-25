# agent/shortsmith.py
"""
⚡ NornPulse: Finishing a generated clip (shortsmith.py)
Norn Labs (nornlabs.ai)

Turns eight seconds of generated footage into something worth watching.

Generated footage arrives bare: no hook text, no voice, often no useful
audio at all. Published like that it is exactly the silent, contextless
filler that the phrase "AI slop" was coined for — and, more practically,
a viewer has nothing to read in the first second, which is the second that
decides whether they stay.

The rest of the pipeline already solves this for cut clips: Skuld burns
kinetic captions, Mímir speaks. Generated clips were skipping both, purely
because they arrive without a transcript to caption. So this module
supplies the missing piece — a spoken line and an on-screen hook derived
from the brief rather than from a transcript — and hands the result to the
same publish path as everything else.

Deliberately not a second rendering engine. It is one ffmpeg pass over an
existing file: burn a hook, lay narration under it, keep whatever audio the
generator produced at a lower level.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# The hook sits high enough to clear a phone's caption overlay and low
# enough to miss the notch. Values are fractions of frame height.
HOOK_Y_FRACTION = 0.13

# A hook longer than this stops being readable in a second, which is all
# the time it gets.
HOOK_MAX_CHARS = 42

# Fallback wrap when the text cannot be measured. Chosen so that even an
# all-caps line of wide glyphs fits a 9:16 frame at HOOK_FONT_DIVISOR.
HOOK_WRAP_WIDTH = 14

# Font height as a fraction of frame height, and how much of the frame width
# the text is allowed to occupy. The margin is not decoration: text touching
# the edge is cropped by some players and by YouTube's own Shorts chrome.
HOOK_FONT_DIVISOR = 18
HOOK_WIDTH_FRACTION = 0.88

# How small the hook may get before it stops being a hook. Below this it is
# better to have a slightly cramped line than an unreadable one.
HOOK_MIN_FONT_PX = 34

# Generator audio is usually ambience rather than content, so it sits under
# the narration rather than competing with it.
FOOTAGE_AUDIO_LEVEL = 0.25


def _font_file() -> Optional[str]:
    """A concrete font file, because drawtext will not resolve family names."""
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/roboto/unhinted/RobotoTTF/Roboto-Black.ttf",
    ):
        if Path(candidate).exists():
            return candidate
    return None


def _escape(text: str) -> str:
    """Escape for ffmpeg's drawtext, which treats several characters specially."""
    out = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’")
    return out.replace("%", "\\%")


def hook_text(brief_title: str) -> str:
    """
    The line burned into the first frames.

    Emoji are dropped rather than rendered: libass and drawtext cannot draw
    colour emoji, and a model writing titles for a comedy channel puts them
    in constantly. Left in, they render as hollow boxes on the one frame
    that has to work.
    """
    stripped = "".join(c for c in brief_title if ord(c) < 0x2190).strip()
    stripped = " ".join(stripped.split())
    if len(stripped) > HOOK_MAX_CHARS:
        stripped = stripped[:HOOK_MAX_CHARS].rsplit(" ", 1)[0] + "…"
    return stripped


def video_size(video_path: str | Path) -> Optional[tuple[int, int]]:
    """Frame dimensions, or None if they cannot be read."""
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0",
             str(video_path)],
            capture_output=True, text=True, timeout=60).stdout.strip()
        w, h = (int(n) for n in out.split(",")[:2])
        return (w, h) if w > 0 and h > 0 else None
    except Exception:
        return None


def _measurer(font_path: Optional[str], font_px: int):
    """A width-in-pixels function for this font, or None if unavailable."""
    if not font_path:
        return None
    try:
        from PIL import ImageFont
        font = ImageFont.truetype(font_path, font_px)
        return font.getlength
    except Exception:
        return None


def fit_hook(text: str, frame_w: int, frame_h: int,
             font_path: Optional[str] = None) -> tuple[list[str], int]:
    """
    Wrap the hook to the frame and pick a font size that actually fits.

    Wrapping by character count is what produced a 722px line in a 720px
    frame: a character budget is a proxy for a pixel budget, and the two
    part company the moment the text is wide-glyphed or upper-case. "Florida
    Lawn Care" is seventeen characters, well inside a twenty-one character
    wrap, and still overflowed — the words ran off both edges of the video
    on the one frame that has to work.

    So the text is measured. If the longest line still will not fit, the
    font shrinks until it does rather than the line being cropped, because a
    smaller hook is readable and a clipped one is not.

    Falls back to a conservative character wrap when the font cannot be
    measured, which is a narrower hook rather than a broken one.
    """
    limit = frame_w * HOOK_WIDTH_FRACTION
    font_px = max(HOOK_MIN_FONT_PX, frame_h // HOOK_FONT_DIVISOR)

    while True:
        width_of = _measurer(font_path, font_px)
        if width_of is None:
            return textwrap.wrap(text, HOOK_WRAP_WIDTH) or [text], font_px

        lines: list[str] = []
        current = ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if current and width_of(candidate) > limit:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        lines = lines or [text]

        if max(width_of(line) for line in lines) <= limit:
            return lines, font_px
        if font_px <= HOOK_MIN_FONT_PX:
            # Out of room to shrink. Break inside the word rather than let it
            # run off the frame: an awkward break is legible, a cropped word
            # is not.
            return _hard_break(lines, width_of, limit), font_px
        # A single word wider than the frame: no wrap can save it, so shrink.
        font_px = max(HOOK_MIN_FONT_PX, int(font_px * 0.9))


def _hard_break(lines: list[str], width_of, limit: float) -> list[str]:
    """
    Split any line still too wide, inside the word.

    Broken into near-equal pieces rather than greedily, because a greedy cut
    leaves an orphan — "Supercalifragilisticexpialidociou" above a lone "s" —
    which looks like a rendering fault rather than a long word.
    """
    out: list[str] = []
    for line in lines:
        if width_of(line) <= limit or len(line) < 2:
            out.append(line)
            continue
        pieces = 2
        while pieces <= len(line):
            size = -(-len(line) // pieces)  # ceiling division
            chunks = [line[i:i + size] for i in range(0, len(line), size)]
            if all(width_of(c) <= limit for c in chunks):
                out.extend(chunks)
                break
            pieces += 1
        else:
            out.append(line)
    return out


def narration_line(brief) -> str:
    """
    What Mímir says over the clip.

    The title, not the angle: the angle is a production note written for a
    generator, and reading it aloud describes the joke instead of telling
    it. Eight seconds is roughly twenty words, so anything longer would be
    cut off mid-sentence.
    """
    line = (brief.caption or brief.title or "").strip()
    words = line.split()
    return " ".join(words[:22])


def finish(
    video_path: str | Path,
    brief,
    clip_id: str,
    out_path: Optional[str | Path] = None,
    narrate: bool = True,
    burn_hook: bool = True,
    energy_level: float = 0.7,
) -> Dict[str, Any]:
    """
    Add a spoken line and an on-screen hook to a generated clip.

    Every step degrades rather than raises. A clip that generated
    successfully must not be lost because a TTS call failed or a font was
    missing — the worst acceptable outcome is the bare clip that would have
    been published anyway.

    Returns a dict describing what was actually applied, so the caller can
    report it honestly instead of assuming.
    """
    video_path = Path(video_path)
    out_path = Path(out_path) if out_path else video_path.with_name(
        f"{video_path.stem}_finished.mp4")
    applied: Dict[str, Any] = {
        "path": video_path, "narrated": False, "hook_burned": False, "hook": ""}

    narration_path = None
    if narrate:
        try:
            from agent.mimir_narrator import MimirNarrator
            spoken = narration_line(brief)
            if spoken:
                narration_path = MimirNarrator().narrate(
                    clip_id=clip_id, script_text=spoken,
                    energy_level=energy_level,
                    output_dir=str(video_path.parent))
        except Exception as e:
            logger.warning(f"Narration unavailable, continuing without it: {e}")

    hook = hook_text(brief.title) if burn_hook else ""
    font = _font_file()
    if hook and not font:
        logger.warning("No usable font found; skipping the burned-in hook.")
        hook = ""

    if not hook and not narration_path:
        logger.info("Nothing to add; leaving the clip as generated.")
        return applied

    cmd = ["ffmpeg", "-v", "error", "-i", str(video_path)]
    if narration_path:
        cmd += ["-i", str(narration_path)]

    filters = []
    if hook:
        # Measured against the real frame, not assumed: a character-count
        # wrap put 722px of text in a 720px frame and ran the hook off both
        # edges.
        size = video_size(video_path) or (1080, 1920)
        lines, font_px = fit_hook(hook, size[0], size[1], font)
        wrapped = "\n".join(lines)
        filters.append(
            f"drawtext=fontfile={font}:text='{_escape(wrapped)}'"
            f":fontcolor=white:fontsize={font_px}:line_spacing=10"
            f":borderw=6:bordercolor=black@0.85"
            f":x=(w-text_w)/2:y=h*{HOOK_Y_FRACTION}"
        )
    if filters:
        cmd += ["-vf", ",".join(filters)]

    if narration_path:
        # Narration on top, generator audio underneath. `dropout_transition`
        # is zeroed so amix does not ramp the level when one input ends —
        # over eight seconds that ramp is audible as a swell.
        cmd += [
            "-filter_complex",
            f"[0:a]volume={FOOTAGE_AUDIO_LEVEL}[bed];"
            f"[1:a]volume=1.6[voice];"
            f"[bed][voice]amix=inputs=2:duration=first:dropout_transition=0[a]",
            "-map", "0:v", "-map", "[a]",
        ] if _has_audio(video_path) else [
            "-map", "0:v", "-map", "1:a", "-shortest",
        ]

    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart",
            "-y", str(out_path)]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    except subprocess.CalledProcessError as e:
        logger.warning(
            f"Finishing pass failed, publishing the raw clip instead: "
            f"{e.stderr.decode('utf-8', 'replace')[:200]}")
        return applied
    except Exception as e:
        logger.warning(f"Finishing pass failed: {e}")
        return applied

    if not out_path.exists() or out_path.stat().st_size == 0:
        logger.warning("Finishing pass produced nothing usable.")
        return applied

    applied.update({
        "path": out_path,
        "narrated": bool(narration_path),
        "hook_burned": bool(hook),
        "hook": hook,
    })
    return applied


def _has_audio(video_path: Path) -> bool:
    """Whether the file has an audio stream at all.

    Veo output sometimes has none, and mixing against a stream that does not
    exist fails the whole pass — which would throw away a clip that was
    perfectly publishable.
    """
    if not shutil.which("ffprobe"):
        return False
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, timeout=60).stdout
        return bool(out.strip())
    except Exception:
        return False
