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
from typing import Any, Dict, Optional, Tuple

from agent import text_fit

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

# How long the hook stays up, and how long it takes to go.
#
# It used to stay up for the whole clip, because the filter had no `enable`
# clause at all. On an eight-second Short built as setup / turn / escalation
# that means three lines of text sitting over the punchline -- the hook's job
# is to buy the first seconds, and after that it is covering the thing it
# sold. Clearing at the turn hands the frame back to the joke.
HOOK_HOLD_SEC = 3.0
HOOK_FADE_SEC = 0.4

# Generator audio is usually ambience rather than content, so it sits under
# the narration rather than competing with it.
FOOTAGE_AUDIO_LEVEL = 0.25


def _font_file() -> Optional[str]:
    """A concrete font file, because drawtext will not resolve family names."""
    return text_fit.font_file()


def _escape(text: str) -> str:
    """Escape for ffmpeg's drawtext, which treats several characters specially."""
    out = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019")
    return out.replace("%", "\\%")


def hook_text(brief_title: str) -> Tuple[str, str]:
    """
    The line burned into the first frames, and a trailing decorative emoji
    run to composite separately as an image.

    A trailing run of emoji tokens is split off before anything else, so
    it survives length truncation of the text around it and gets its own
    render path \u2014 libass and drawtext still cannot draw colour emoji
    directly, and a model writing titles for a comedy channel puts them in
    constantly. Anything other than a trailing run (glued mid-word, or
    leading) is rarer in practice and is still just dropped: left in the
    burned text, an emoji renders as a hollow box on the one frame that has
    to work.
    """
    plain, emoji_suffix = text_fit.split_trailing_emoji(brief_title)
    stripped = text_fit.strip_emoji(plain)
    if len(stripped) > HOOK_MAX_CHARS:
        stripped = stripped[:HOOK_MAX_CHARS].rsplit(" ", 1)[0] + "\u2026"
        # The line was cut before the emoji's natural position; tacking it
        # onto a truncated, unrelated word would read as a mistake.
        emoji_suffix = ""
    return stripped, emoji_suffix


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


def fit_hook(text: str, frame_w: int, frame_h: int,
             font_path: Optional[str] = None) -> tuple[list[str], int]:
    """
    Wrap the hook to the frame and pick a font size that actually fits.

    The frame-relative half of the calculation; the measuring itself lives
    in agent.text_fit, because Skuld's banner had the same bug and the two
    should not drift apart again.
    """
    return text_fit.fit_text(
        text,
        max_width_px=frame_w * HOOK_WIDTH_FRACTION,
        font_px=max(HOOK_MIN_FONT_PX, frame_h // HOOK_FONT_DIVISOR),
        font_path=font_path,
        min_font_px=HOOK_MIN_FONT_PX,
        fallback_wrap=HOOK_WRAP_WIDTH,
    )


def narration_line(brief) -> str:
    """
    What Mímir says over the clip.

    The title, not the angle: the angle is a production note written for a
    generator, and reading it aloud describes the joke instead of telling
    it. Eight seconds is roughly twenty words, so anything longer would be
    cut off mid-sentence.

    Hashtags and emoji are stripped before the line is spoken. A caption is
    written to be *read* under a Short, so it ends in "#aislop #comedy
    #unboxing" — and a TTS model handed that says the words out loud. The
    first clip off this path narrated its own hashtags over the punchline.
    """
    line = (brief.caption or brief.title or "").strip()
    line = "".join(c for c in line if ord(c) < 0x2190)
    words = [w for w in line.split() if not w.startswith("#")]
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

    hook, hook_emoji = hook_text(brief.title) if burn_hook else ("", "")
    font = _font_file()
    if hook and not font:
        logger.warning("No usable font found; skipping the burned-in hook.")
        hook, hook_emoji = "", ""

    if not hook and not narration_path:
        logger.info("Nothing to add; leaving the clip as generated.")
        return applied

    filters = []
    emoji_path: Optional[Path] = None
    emoji_pos: Optional[Tuple[float, float]] = None
    if hook:
        # Measured against the real frame, not assumed: a character-count
        # wrap put 722px of text in a 720px frame and ran the hook off both
        # edges.
        size = video_size(video_path) or (1080, 1920)
        lines, font_px = fit_hook(hook, size[0], size[1], font)

        line_height = font_px + 10
        # Held, then faded out rather than cut: an instant disappearance
        # mid-shot reads as a glitch, and 0.4s is short enough not to
        # linger over the turn.
        alpha = (f"if(lt(t,{HOOK_HOLD_SEC - HOOK_FADE_SEC:.2f}),1,"
                 f"({HOOK_HOLD_SEC:.2f}-t)/{HOOK_FADE_SEC:.2f})")

        # A trailing emoji, if any, sits beside the LAST line, and the two
        # are centred together as one group — the text alone re-centred
        # with the emoji bolted onto whatever room happens to be left reads
        # as two unrelated things, not one hook.
        last_line_x: Optional[float] = None
        if hook_emoji:
            last_line_y = size[1] * HOOK_Y_FRACTION + (len(lines) - 1) * line_height
            emoji_path, emoji_pos, last_line_x = _place_trailing_emoji(
                hook_emoji, lines[-1], font, font_px, size[0], last_line_y,
                out_dir=video_path.parent, clip_id=clip_id)

        # One drawtext per line, each centred on its own width.
        #
        # A single drawtext holding embedded newlines centres the *block*
        # and left-aligns every line inside it, so a three-line hook hangs
        # its shorter lines against the left edge and reads as a layout
        # fault. Skuld's banner hit this and fixed it the same way; this
        # path kept the old shape and shipped the ragged version.
        for i, line in enumerate(lines):
            is_last = i == len(lines) - 1
            x_expr = (f"{last_line_x:.1f}" if is_last and last_line_x is not None
                      else "(w-text_w)/2")
            filters.append(
                f"drawtext=fontfile={font}:text='{_escape(line)}'"
                f":fontcolor=white:fontsize={font_px}"
                f":borderw=6:bordercolor=black@0.85"
                f":alpha='{alpha}'"
                f":enable='lt(t,{HOOK_HOLD_SEC:.2f})'"
                f":x={x_expr}:y=h*{HOOK_Y_FRACTION}+{i * line_height}"
            )

    cmd = ["ffmpeg", "-v", "error", "-i", str(video_path)]
    if narration_path:
        cmd += ["-i", str(narration_path)]
    if emoji_path:
        cmd += ["-i", str(emoji_path)]
    emoji_idx = 1 + (1 if narration_path else 0)

    # The audio side, described but not yet attached to the command —
    # merged below into one -filter_complex when the video side also needs
    # one (the emoji overlay), kept as the original separate -vf/
    # -filter_complex split otherwise so the already-verified no-emoji path
    # never changes shape.
    audio_graph = None
    audio_map = None
    audio_extra: list = []
    if narration_path:
        # Narration on top, generator audio underneath. `dropout_transition`
        # is zeroed so amix does not ramp the level when one input ends —
        # over eight seconds that ramp is audible as a swell.
        if _has_audio(video_path):
            audio_graph = (
                f"[0:a]volume={FOOTAGE_AUDIO_LEVEL}[bed];"
                f"[1:a]volume=1.6[voice];"
                f"[bed][voice]amix=inputs=2:duration=first:dropout_transition=0[a]")
            audio_map = "[a]"
        else:
            audio_map = "1:a"
            audio_extra = ["-shortest"]

    if emoji_path and filters:
        ex, ey = emoji_pos
        video_graph = (
            f"[0:v]{','.join(filters)}[hooked];"
            f"[hooked][{emoji_idx}:v]overlay=x={ex:.1f}:y={ey:.1f}"
            # ponytail: hard cut, not the text's alpha fade — ffmpeg's
            # overlay has no native time-varying alpha; add via
            # colorchannelmixer+enable if the cut reads as a glitch.
            f":enable='lt(t,{HOOK_HOLD_SEC:.2f})'[vout]")
        cmd += ["-filter_complex",
                ";".join([video_graph] + ([audio_graph] if audio_graph else [])),
                "-map", "[vout]"]
        cmd += ["-map", audio_map] if audio_map else ["-map", "0:a?"]
        cmd += audio_extra
    else:
        if filters:
            cmd += ["-vf", ",".join(filters)]
        if audio_graph:
            cmd += ["-filter_complex", audio_graph, "-map", "0:v", "-map", audio_map]
        elif audio_map:
            cmd += ["-map", "0:v", "-map", audio_map] + audio_extra
        # else: no explicit -map at all; ffmpeg auto-selects and keeps the
        # original audio, unchanged from before this existed.

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
        "hook_emoji": hook_emoji if emoji_path else "",
    })
    return applied


def _place_trailing_emoji(
    emoji_text: str, last_line: str, font: Optional[str], font_px: int,
    frame_w: int, y: float, out_dir: Path, clip_id: str,
) -> Tuple[Optional[Path], Optional[Tuple[float, float]], Optional[float]]:
    """
    Render hook_emoji as a PNG and work out where it and the hook's last
    line both sit, centred as one group rather than the text re-centred on
    its own and the emoji bolted onto whatever room is left.

    Returns (png_path, (emoji_x, emoji_y), last_line_x) — all None if the
    glyph could not be rendered, or fitting it would push the group past
    the frame's width budget, in which case the caller keeps the bare text
    exactly as if the emoji had never been there.
    """
    glyph = text_fit.emoji_glyph(emoji_text, font_px)
    if not glyph:
        return None, None, None
    img, advance = glyph

    width_of = text_fit.measurer(font, font_px)
    if width_of is None:
        return None, None, None
    text_w = width_of(last_line)
    gap = font_px * 0.28
    group_w = text_w + gap + advance
    if group_w > frame_w * HOOK_WIDTH_FRACTION:
        return None, None, None

    group_x = (frame_w - group_w) / 2
    png_path = out_dir / f"{clip_id}_hook_emoji.png"
    img.save(png_path)
    emoji_x = group_x + text_w + gap
    emoji_y = y + (font_px - img.height) / 2
    return png_path, (emoji_x, emoji_y), group_x


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
