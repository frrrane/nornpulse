# agent/skuld_renderer.py
"""
⚡ NornPulse: Skuld Video Studio Renderer (skuld_renderer.py)
Norn Labs (nornlabs.ai)

Responsible for compiling 16:9 source videos into viral 9:16 vertical shorts,
applying dynamic kinetic subtitles with relative timeline re-basing, crop
positioning, camera motion, color grading, and hook title banners. crop_mode,
motion_effect, and color_grade are Urðr-grounded per hook_type (see
agent.urdr_analytics.get_top_visual_benchmark) rather than chosen ad hoc
per render. Subtitle and banner styling is driven by two directional sliders:

  warmth (0.0-1.0): cool blue/white  ->  warm gold/orange color grade
  crazy  (0.0-1.0): subtle, static text  ->  bouncing, wobbling kinetic text
"""

import colorsys
import os
import re
import subprocess
import logging
import time
from pathlib import Path
from typing import Dict, Any, Optional, Literal, Tuple, List

from agent import text_fit

logger = logging.getLogger("nornpulse.skuld")

# Caption typeface. Must name a real heavy/display weight rather than a
# generic "sans-serif" left for fontconfig to resolve however the host
# happens to be configured -- burned-in captions over video live or die
# on that weight.
#
# It's an env var because the right answer differs per environment and
# getting it wrong is SILENT: libass substitutes without warning. On a
# dev box with MS core fonts, "Arial Black" resolves correctly. In the
# container it does not exist, and fontconfig was measured falling all
# the way back to `DejaVu Sans "Book"` -- i.e. REGULAR weight, visibly
# lighter than intended, with nothing logged anywhere. The Dockerfile
# installs Roboto Black and sets CAPTION_FONT accordingly.
#
# Verify a host's resolution with: fc-match "<font name>"
CAPTION_FONT = os.getenv("CAPTION_FONT", "Arial Black")

# Selectable caption faces. Each must be installed in the image (see the
# Dockerfile) or libass substitutes silently and the render comes out in a
# different, usually lighter, face with nothing logged.
CAPTION_FONTS = {
    "Impact — Roboto Black": "Roboto Black",
    "Condensed — Roboto Condensed": "Roboto Condensed",
    "Geometric — League Spartan": "League Spartan",
    "Humanist — Lato Black": "Lato Black",
    "Neutral — DejaVu Sans": "DejaVu Sans",
}


def resolve_caption_font(choice: Optional[str] = None) -> str:
    """
    The font to name in the ASS style.

    A name libass cannot resolve is not an error — it substitutes and
    carries on — so an unknown choice falls back to the configured default
    rather than reaching the renderer and quietly changing the look.
    """
    if not choice:
        return CAPTION_FONT
    return CAPTION_FONTS.get(choice, CAPTION_FONT)

CropMode = Literal["center_crop", "blurred_background", "top_anchored_crop", "cinematic_letterbox"]

# Crop modes that discard the sides of the frame. center_crop fills a 9:16
# screen by cutting a 16:9 source down to `crop=ih*9/16:ih`, which is the
# right trade for centred action and the wrong one for a source carrying
# full-width burned-in graphics -- a NASA explainer rendered this way turned
# "LUNAR LANDINGS" into "NAR LANDIN". The other three scale to fit and pad,
# so they keep every pixel of width at the cost of filling less of the
# screen. Neither is correct in general; it depends on the footage.
SIDE_CROPPING_MODES = ("center_crop",)
MotionEffect = Literal["none", "ken_burns_zoom", "punch_in_zoom", "shake"]
ColorGrade = Literal["neutral", "cool_desaturated", "warm_glow", "vibrant_punch"]
RGB = Tuple[int, int, int]


def parse_time_to_seconds(time_str: str) -> float:
    """Converts HH:MM:SS, MM:SS, or SS.ms into float seconds."""
    parts = time_str.replace(",", ".").split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def format_seconds_to_mmss(total_seconds: float) -> str:
    """Formats seconds as MM:SS for use in prompts and UI text."""
    total_seconds = max(0, int(total_seconds))
    m, s = divmod(total_seconds, 60)
    return f"{m:02d}:{s:02d}"


def get_video_duration_seconds(video_path: str | Path) -> float:
    """
    Reads the actual duration of a video file via ffprobe (bundled with
    FFmpeg — no separate dependency needed). Used so the pipeline works
    with source videos of any length instead of a hardcoded assumption.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"ffprobe failed to read duration for {video_path}: {result.stderr}")
    return float(result.stdout.strip())


def get_video_dimensions(video_path: str | Path, default: Tuple[int, int] = (1920, 1080)) -> Tuple[int, int]:
    """
    Reads the source's native (width, height) via ffprobe. Needed for the
    zoompan-based motion effects: zoompan's `s=` output size is a literal
    WxH, not an expression that can reference the input's own iw/ih like
    scale/crop can -- so the zoom pre-stage (see _build_zoom_prestage) has
    to zoom at the source's own real resolution to avoid stretching it.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    raw = result.stdout.strip()
    if result.returncode != 0 or "," not in raw:
        return default
    try:
        w_str, h_str = raw.split(",")[:2]
        return int(w_str), int(h_str)
    except ValueError:
        return default


def get_video_fps(video_path: str | Path, default: float = 30.0) -> float:
    """
    Reads the source's actual frame rate via ffprobe, for the zoompan-based
    motion effects (see _build_motion_filter) -- zoompan takes an explicit
    output fps, and mismatching it against the real source rate causes
    frame interpolation/duplication that can visibly judder or drift the
    clip's duration slightly. r_frame_rate comes back as a "num/den"
    fraction (e.g. "30000/1001" for 29.97fps), not a plain float.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate",
        "-of", "csv=p=0",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    raw = result.stdout.strip()
    if result.returncode != 0 or not raw:
        return default
    try:
        if "/" in raw:
            num, den = raw.split("/")
            return float(num) / float(den) if float(den) != 0 else default
        return float(raw)
    except (ValueError, ZeroDivisionError):
        return default


def has_audio_stream(video_path: str | Path) -> bool:
    """
    Checks via ffprobe whether the source has an audio stream at all —
    some sources (silent b-roll, music-video-style clips) don't. Needed
    before referencing [0:a] in a filter graph, since FFmpeg errors out
    if that stream specifier matches nothing.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "csv=p=0",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return bool(result.stdout.strip())


# Below this mean volume (dB), a clip's original audio is treated as
# likely too quiet to reliably follow, triggering Mimir's narration
# fallback. This is a loudness heuristic, not a true speech-intelligibility
# classifier — a loud but heavily mumbled or accented voice would still
# measure fine here and wouldn't get flagged. Calibrated empirically
# against the demo source: real, clearly-narrated speech throughout it
# measures -29 to -35 dB; the same audio deliberately quietened to 5%
# volume (genuinely hard to hear) measures -55.7 dB. -42 dB sits with
# margin on both sides of that gap.
NARRATION_FALLBACK_VOLUME_THRESHOLD_DB = -42.0


def measure_audio_mean_volume(video_path: str | Path, start_time: str, end_time: str) -> Optional[float]:
    """
    Measures the mean audio volume (dB) of a specific time window via
    FFmpeg's volumedetect filter. Returns None if the source has no audio
    stream at all, or if FFmpeg's output couldn't be parsed.
    """
    if not has_audio_stream(video_path):
        return None
    cmd = [
        "ffmpeg", "-ss", str(start_time), "-to", str(end_time),
        "-i", str(video_path), "-af", "volumedetect", "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    match = re.search(r"mean_volume:\s*(-?\d+\.?\d*)\s*dB", result.stderr)
    return float(match.group(1)) if match else None


def seconds_to_ass_time(total_seconds: float) -> str:
    """
    Converts total seconds into ASS time format 'H:MM:SS.cs'.

    Rounded to centiseconds *first*, then decomposed. Rounding the
    fractional part on its own overflows: at 9.999 the fraction rounds to
    100 centiseconds, which had nowhere to carry and printed as
    "0:00:09.100" — three digits in a field that takes two. ASS timestamps
    are fixed-width, so libass reads the malformed value as a different
    time, and a caption lands somewhere it was never meant to. Every
    caption whose edge fell in the last 5ms of a second was affected, and
    at 59.999 the minute was wrong too.
    """
    if total_seconds < 0:
        total_seconds = 0
    cs_total = int(round(total_seconds * 100))
    h, rem = divmod(cs_total, 360_000)
    m, rem = divmod(rem, 6_000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _lerp_rgb(c1: RGB, c2: RGB, t: float) -> RGB:
    t = _clamp01(t)
    return tuple(round(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))  # type: ignore[return-value]


def _lerp_rgb_via_hsv(c1: RGB, c2: RGB, t: float) -> RGB:
    """
    Interpolates two colors through HSV (taking the shorter way around the
    hue wheel) instead of straight-line RGB. Plain RGB lerp between hues
    that are far apart — e.g. cyan and orange — passes through a
    desaturated gray at the midpoint; HSV lerp stays vivid the whole way,
    which matters for a caption highlight color that has to read as a
    punchy accent at every warmth setting, not just at the two endpoints.
    """
    t = _clamp01(t)
    h1, s1, v1 = colorsys.rgb_to_hsv(c1[0] / 255, c1[1] / 255, c1[2] / 255)
    h2, s2, v2 = colorsys.rgb_to_hsv(c2[0] / 255, c2[1] / 255, c2[2] / 255)
    if abs(h2 - h1) > 0.5:
        if h2 > h1:
            h1 += 1.0
        else:
            h2 += 1.0
    h = (h1 + (h2 - h1) * t) % 1.0
    s = s1 + (s2 - s1) * t
    v = v1 + (v2 - v1) * t
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (round(r * 255), round(g * 255), round(b * 255))


def _rgb_to_ass_color(rgb: RGB, alpha: int = 0x00) -> str:
    """ASS colors are &HAABBGGRR (alpha, blue, green, red)."""
    r, g, b = rgb
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def _rgb_to_ffmpeg_hex(rgb: RGB) -> str:
    r, g, b = rgb
    return f"0x{r:02X}{g:02X}{b:02X}"


def _escape_ass_text(text: str) -> str:
    """
    Escapes characters libass interprets as override-tag delimiters, so a
    literal '{' or '}' in transcript text doesn't get parsed as an ASS
    styling directive and corrupt or hide the line.
    """
    return text.replace("{", "\\{").replace("}", "\\}")


# The hook banner. Sized in pixels against a 1080-wide frame: the box sits
# inset from both edges, and the text is inset again inside the box, because
# text touching a box edge reads as an overflow even when it technically
# fits.
BANNER_X = 40
BANNER_Y = 80
BANNER_WIDTH = 1000
BANNER_PADDING = 24
# Sized for a phone held at arm's length, not for a desktop preview. 42px
# was legible and unassertive, and a reviewer asked for bigger -- a hook
# banner competes with moving video and is read in about a second.
BANNER_FONT_PX = 64
BANNER_MIN_FONT_PX = 36
BANNER_FALLBACK_WRAP = 24

# A clip lifted out of the middle of a longer video starts and stops in the
# middle of a shot, which reads as an accident rather than an edit -- a
# reviewer asked for the transition to be "more seamless". Short enough not
# to spend the first second of a twelve-second clip fading in.
EDGE_FADE_SEC = 0.35


def _escape_drawtext(text: str) -> str:
    """
    Escapes characters FFmpeg's drawtext filter treats as special:
    colon (option separator), percent (strftime expansion), backslash
    and single quote (escape characters).
    """
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("%", "\\%")
    text = text.replace("'", "\u2019")  # swap to a typographic apostrophe rather than dropping it
    return text


def _kinetic_prefix(crazy: float) -> str:
    """
    Builds an ASS override-tag prefix that pops a chunk of caption text in
    with a scale bounce, and adds a slight rotational wobble at higher
    intensity. Applied per word-chunk (see _chunk_words) rather than once
    per full line, so the bounce is felt continuously through a clip
    instead of a single pop at the very start of a multi-second sentence.
    Returns "" at low crazy values so default captions stay clean/static.
    """
    crazy = _clamp01(crazy)
    if crazy <= 0.05:
        return ""

    pop_scale = 100 + round(60 * crazy)          # up to 160% overshoot at max
    settle_ms = int(180 + 120 * (1 - crazy))      # snappier pop at higher intensity
    tags = (
        f"\\t(0,{settle_ms},\\fscx{pop_scale}\\fscy{pop_scale})"
        f"\\t({settle_ms},{settle_ms + 120},\\fscx100\\fscy100)"
    )
    if crazy > 0.6:
        wobble_deg = round(5 * crazy)
        tags += (
            f"\\t(0,{settle_ms},\\frz{wobble_deg})"
            f"\\t({settle_ms},{settle_ms + 120},\\frz0)"
        )
    return "{" + tags + "}"


# Whether to split a transcript line into word-chunks that reveal in
# sequence, or show the line as one caption for its whole window.
#
# The kinetic reveal is the nicer look and it is off, because it cannot be
# done honestly with the timing data available. A transcript line carries a
# start time and nothing else, so each chunk's moment is guessed by
# character count across a window that runs to the *next* line's start --
# and the speaker finishes before then and pauses. Measured against the
# clip's own audio, the chunks lagged by about one chunk by mid-line: the
# caption read "were missing." while the audio said "And we didn\'t really".
# Three clips were rejected for it.
#
# One caption per line cannot drift within itself. Turning this back on
# needs real word-level timings from transcription, not a better guess.
WORD_CHUNK_CAPTIONS = False


def _words_per_chunk(crazy: float) -> int:
    """
    How many words reveal at once, per caption event. Ties directly into
    "craziness": calm (crazy=0) shows ~5 words at a time, close to a full
    clause; max craziness (crazy=1) drops to single-word pops, the rapid
    CapCut/Opus-Clip-style reveal. This is what makes crazy's effect
    continuous and visible throughout a clip, not just one bounce at the
    very start of each sentence.
    """
    crazy = _clamp01(crazy)
    return max(1, round(5 - 4 * crazy))


def _chunk_words(text: str, words_per_chunk: int) -> List[str]:
    words = text.split()
    return [" ".join(words[i:i + words_per_chunk]) for i in range(0, len(words), words_per_chunk)]


# Below this a surviving fragment of a boundary-straddling chunk flashes
# instead of reading, so it is dropped rather than shown.
MIN_VISIBLE_CHUNK_SEC = 0.18


def _distribute_chunk_times(
    chunks: List[str], rel_start: float, rel_end: float, min_chunk_dur: float = 0.28,
) -> List[Tuple[float, float]]:
    """
    Splits a line's [rel_start, rel_end] window across its word-chunks,
    proportional to each chunk's character count (a lightweight stand-in
    for real per-word ASR timing, which the transcript doesn't carry).
    min_chunk_dur keeps very short chunks (e.g. a single short word) from
    flashing illegibly fast; if the floors would collectively overflow the
    line's actual duration, everything is scaled back down proportionally
    so the last chunk never reads past rel_end.
    """
    if not chunks:
        return []
    available = max(0.01, rel_end - rel_start)
    total_chars = sum(max(1, len(c)) for c in chunks)
    raw_durations = [max(min_chunk_dur, available * (max(1, len(c)) / total_chars)) for c in chunks]
    total_raw = sum(raw_durations)
    if total_raw > available:
        raw_durations = [d * (available / total_raw) for d in raw_durations]

    times = []
    cursor = rel_start
    for d in raw_durations:
        end = min(rel_end, cursor + d)
        times.append((cursor, end))
        cursor = end
    return times


def _highlight_emphasis_word(chunk_text: str, secondary_bgr_hex: str) -> str:
    """
    Colors the single longest word in a chunk with the warmth-driven
    secondary color, so SecondaryColour actually renders somewhere on
    screen — ASS only honors it via \\k karaoke tags otherwise, which this
    codebase never emits. Words under 4 letters (stripped of punctuation)
    are skipped as not punchy enough to bother emphasizing.
    """
    words = chunk_text.split()
    if not words:
        return chunk_text
    idx = max(range(len(words)), key=lambda i: len(re.sub(r"[^\w]", "", words[i])))
    if len(re.sub(r"[^\w]", "", words[idx])) < 4:
        return chunk_text
    words[idx] = f"{{\\c&H{secondary_bgr_hex}&}}{words[idx]}{{\\c}}"
    return " ".join(words)


def _build_style_line(warmth: float, crazy: float, caption_font: Optional[str] = None) -> Tuple[str, str]:
    """
    Builds the ASS [V4+ Styles] line, color-graded by warmth and sized by
    crazy. Returns (style_line, secondary_bgr_hex) — the latter for
    _highlight_emphasis_word, since SecondaryColour itself never renders
    (ASS only honors it via \\k karaoke tags, which this pipeline doesn't
    use) so the actual color has to be reapplied inline per emphasis word.
    """
    warmth = _clamp01(warmth)
    crazy = _clamp01(crazy)

    # Cool icy blue-white at 0.0 -> warm gold at 1.0, matching the UI's
    # documented range (previously lerped from pure white, so low warmth
    # looked identical to "off" instead of visibly cool).
    primary_rgb = _lerp_rgb((205, 230, 255), (255, 196, 84), warmth)
    # Emphasis-word highlight: cool cyan -> hot orange-red, kept visually
    # distinct from primary across the whole warmth range so the
    # highlighted word always reads as an accent, not a shade of the same
    # color as the rest of the chunk. HSV lerp (not _lerp_rgb) so the
    # midpoint stays a vivid magenta/pink instead of a desaturated gray —
    # cyan and orange-red are far enough apart in hue that a straight RGB
    # average washes out.
    secondary_rgb = _lerp_rgb_via_hsv((110, 210, 255), (255, 70, 20), warmth)

    primary_ass = _rgb_to_ass_color(primary_rgb)
    secondary_ass = _rgb_to_ass_color(secondary_rgb)
    secondary_bgr_hex = f"{secondary_rgb[2]:02X}{secondary_rgb[1]:02X}{secondary_rgb[0]:02X}"

    fontsize = 54 + round(14 * crazy)   # 54 -> 68
    outline = 3 + round(2 * crazy)      # 3 -> 5
    shadow = 2 + round(2 * crazy)       # 2 -> 4

    style_line = (
        f"Style: KineticViral,{caption_font or CAPTION_FONT},{fontsize},{primary_ass},{secondary_ass},"
        f"&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,{outline},{shadow},2,50,50,300,1"
    )
    return style_line, secondary_bgr_hex


def generate_rebased_ass_subtitle_file(
    transcript_text: str,
    output_ass_path: str | Path,
    clip_start_sec: float,
    clip_end_sec: float,
    warmth: float = 0.5,
    crazy: float = 0.3,
    caption_font: Optional[str] = None,
) -> Path:
    """
    Parses transcript lines, rebases timestamps relative to the clip window
    starting at 0, and generates a color-graded, kinetically-animated ASS
    subtitle file for FFmpeg. Each line is broken into word-chunks (sized
    by crazy — see _words_per_chunk) that reveal in sequence rather than
    the whole line popping in as one static block, with one word per
    chunk highlighted in the warmth-driven accent color.
    """
    output_ass_path = Path(output_ass_path)
    output_ass_path.parent.mkdir(parents=True, exist_ok=True)

    style_line, secondary_bgr_hex = _build_style_line(warmth, crazy, caption_font)
    kinetic_prefix = _kinetic_prefix(crazy)
    words_per_chunk = _words_per_chunk(crazy) if WORD_CHUNK_CAPTIONS else 0

    ass_content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style_line}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"""

    clip_duration = clip_end_sec - clip_start_sec

    # Pass 1: parse every line's absolute start (and explicit end, if the
    # transcript gives one). Lines without an explicit end need a second
    # pass to know where the NEXT line starts, so a fast-paced transcript
    # never guesses a duration that bleeds into it — the bug that caused
    # two captions to render stacked on top of each other simultaneously.
    parsed: List[Dict[str, Any]] = []
    for line in transcript_text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        times = re.findall(r"(?:\[)?(\d{1,2}:\d{2}(?:[:.]\d+)?)(?:\])?", line)
        if not times:
            continue
        abs_start = parse_time_to_seconds(times[0])
        explicit_end = parse_time_to_seconds(times[1]) if len(times) >= 2 else None
        parsed.append({"abs_start": abs_start, "explicit_end": explicit_end, "raw_line": line})

    # Pass 2: resolve each line's effective end, capping any guessed
    # (non-explicit) duration at the next line's start.
    DEFAULT_LINE_DUR = 4.0
    for i, entry in enumerate(parsed):
        if entry["explicit_end"] is not None:
            entry["abs_end"] = entry["explicit_end"]
            continue
        guessed_end = entry["abs_start"] + DEFAULT_LINE_DUR
        if i + 1 < len(parsed):
            guessed_end = min(guessed_end, parsed[i + 1]["abs_start"])
        # Never let the cap collapse a line to zero/negative duration if
        # two lines share (or nearly share) a start timestamp.
        entry["abs_end"] = max(entry["abs_start"] + 0.1, guessed_end)

    lines_written = 0
    for entry in parsed:
        abs_start, abs_end, line = entry["abs_start"], entry["abs_end"], entry["raw_line"]

        # Inclusive overlap check: catches any line overlapping the clip window
        if max(abs_start, clip_start_sec) >= min(abs_end, clip_end_sec):
            continue

        # The line's true position, NOT yet clamped to the clip.
        #
        # Clamping first and distributing afterwards is what desynced the
        # captions. A line starting a second before the cut had its words
        # squeezed into the remaining fraction, so text whose audio had
        # already played appeared at 0.00 and raced; a line running past
        # the end was crushed the same way, giving 0.16s chunks at the
        # tail. Words are laid out at their real pace here and trimmed
        # afterwards, so what survives sits where it is actually spoken.
        rel_start = abs_start - clip_start_sec
        rel_end = abs_end - clip_start_sec

        # Strip bracketed timestamp tags, e.g. "[00:12 - 00:16]"
        clean_text = re.sub(r"\[.*?\]", "", line)
        # Strip any leftover raw timestamps, e.g. "00:12:03,450"
        clean_text = re.sub(r"\d{1,2}:\d{2}(?:[:.]\d+)?", "", clean_text)
        # Strip only an SRT-style arrow separator, never bare words
        clean_text = re.sub(r"-->", "", clean_text)
        # Strip a lone connecting hyphen only when isolated by whitespace
        # (leftover from "00:12 - 00:16"); never touches hyphens inside
        # real words like "well-known"
        clean_text = re.sub(r"(?<=\s)-(?=\s)", "", clean_text)
        clean_text = clean_text.strip(" []:")
        # Escape any literal '{'/'}' from the transcript itself BEFORE
        # _highlight_emphasis_word injects its own real override-tag
        # braces below — escaping afterward would turn those injected
        # tags into visible literal text instead of a color directive.
        clean_text = _escape_ass_text(clean_text)

        if not clean_text:
            continue

        # words_per_chunk == 0 means "do not split": the line is one caption
        # held across its own window, which is the only claim the timing data
        # actually supports.
        chunks = (_chunk_words(clean_text, words_per_chunk)
                  if words_per_chunk else [clean_text])
        chunk_times = _distribute_chunk_times(chunks, rel_start, rel_end)

        for chunk_text, (chunk_start, chunk_end) in zip(chunks, chunk_times):
            # Drop chunks spoken outside the clip, and trim the one that
            # straddles each edge rather than sliding it inward.
            if chunk_end <= 0.0 or chunk_start >= clip_duration:
                continue
            chunk_start = max(0.0, chunk_start)
            chunk_end = min(clip_duration, chunk_end)
            if chunk_end - chunk_start < MIN_VISIBLE_CHUNK_SEC:
                # A sliver left at an edge flashes rather than reads.
                continue
            if chunk_end <= chunk_start:
                continue
            highlighted = _highlight_emphasis_word(chunk_text, secondary_bgr_hex)
            start_ass = seconds_to_ass_time(chunk_start)
            end_ass = seconds_to_ass_time(chunk_end)
            ass_content += f"Dialogue: 0,{start_ass},{end_ass},KineticViral,,0,0,0,,{kinetic_prefix}{highlighted}\n"
            lines_written += 1

    if lines_written == 0:
        logger.warning(
            f"No subtitle lines matched clip window {clip_start_sec}-{clip_end_sec}s. "
            f"Check that transcript timestamps overlap this range."
        )

    with open(output_ass_path, "w", encoding="utf-8-sig") as f:
        f.write(ass_content)

    return output_ass_path


class SkuldRenderer:
    """
    Executes FFmpeg rendering pipelines to transform 16:9 videos into 9:16 shorts.
    """

    def __init__(self, output_dir: str | Path = "output_clips"):
        # Resolve to an absolute path up front so the FFmpeg subprocess
        # (which may run with a different CWD) always finds the .ass file.
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _build_crop_filter(self, crop_mode: CropMode, video_label: str = "[0:v]") -> str:
        """
        video_label is normally the raw decoded input ([0:v]), but when a
        zoom motion effect is active, render_vertical_short instead pipes
        in the already zoom-panned stream (see _build_zoom_prestage) so
        every crop_mode composites from the zoomed footage rather than the
        static original.

        blurred_background/top_anchored_crop reference video_label twice
        (once for the blurred bg layer, once for the sharp fg layer). A
        bare demuxer stream like [0:v] can be referenced any number of
        times for free — ffmpeg auto-taps it — but a named filter pad
        like [zoomed] is a normal one-consumer link, so referencing it
        twice without splitting first throws "Invalid stream specifier"
        (confirmed live). Only split when video_label is actually such a
        named pad.
        """
        needs_split = video_label != "[0:v]" and crop_mode in ("blurred_background", "top_anchored_crop")
        if needs_split:
            split_prefix = f"{video_label}split=2[_bgsrc][_fgsrc];"
            bg_label, fg_label = "[_bgsrc]", "[_fgsrc]"
        else:
            split_prefix, bg_label, fg_label = "", video_label, video_label

        # force_original_aspect_ratio=increase deliberately overflows one
        # dimension to fully COVER the 1080x1920 box while preserving the
        # source's aspect ratio (like CSS background-size:cover) -- for a
        # 16:9 source that produces a 3413x1920 image, not 1080x1920.
        # cropping down to 1080x1920 BEFORE blurring (not after) fixes two
        # things found live: (1) libx264 otherwise rejects the
        # (frequently odd) overflowed width outright -- "width not
        # divisible by 2 (3413x1920)" -- and (2) blurring the full
        # oversized 3413x1920 image cost ~72s for an 8s clip; blurring the
        # already-cropped 1080x1920 image (~3.2x fewer pixels) cut that to
        # ~25s. A lighter 20:6 radius (vs. the original 40:10) brought it
        # down further to ~17s, in line with the other crop_modes, while
        # still reading as clearly out-of-focus.
        if crop_mode == "blurred_background":
            return (
                f"{split_prefix}"
                f"{bg_label}scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:6[bg];"
                f"{fg_label}scale=1080:-1:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
            )
        if crop_mode == "top_anchored_crop":
            # Same blurred-background composition, but the foreground is
            # anchored near the top of the frame instead of vertically
            # centered -- keeps faces/action in the upper two-thirds so the
            # burned-in captions (which sit low) never overlap the subject.
            return (
                f"{split_prefix}"
                f"{bg_label}scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:6[bg];"
                f"{fg_label}scale=1080:-1:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:H*0.06"
            )
        if crop_mode == "cinematic_letterbox":
            # Full frame scaled to fit width, solid black bars top/bottom --
            # a deliberate, moody "film" look rather than blurred_background's
            # softer, more neutral fill.
            return (
                f"{video_label}scale=1080:-1:force_original_aspect_ratio=decrease,"
                f"pad=1080:1920:(1080-iw)/2:(1920-ih)/2:color=black"
            )
        return f"{video_label}crop=ih*9/16:ih,scale=1080:1920"

    def _build_color_grade_filter(self, color_grade: ColorGrade) -> str:
        """
        Grades the actual video pixels per sentiment -- distinct from the
        warmth slider, which only tints captions/banner. Chained as a plain
        comma-continuation of whatever _build_crop_filter produced, so it
        works uniformly regardless of crop_mode.
        """
        if color_grade == "cool_desaturated":
            return ",eq=saturation=0.75:contrast=1.15:brightness=-0.02,colorbalance=rs=-0.05:gs=-0.02:bs=0.08"
        if color_grade == "warm_glow":
            return ",eq=saturation=1.1:contrast=0.95:brightness=0.02:gamma=1.05,colorbalance=rs=0.06:gs=0.02:bs=-0.05"
        if color_grade == "vibrant_punch":
            return ",eq=saturation=1.35:contrast=1.2"
        return ""

    def _build_zoom_prestage(
        self, motion_effect: MotionEffect, clip_duration_sec: float, crazy: float,
        source_w: int, source_h: int, fps: float,
    ) -> str:
        """
        Zooms the RAW decoded input via `zoompan` and labels the result
        [zoomed], for render_vertical_short to hand to _build_crop_filter
        in place of [0:v] — every crop_mode then composites from already
        zoomed footage. Returns "" for a non-zoom motion_effect.

        This has to run zoompan on the ORIGINAL frame (at its own native
        resolution, not the eventual 1080x1920 canvas) rather than after
        crop_mode has done its work, for two reasons found by testing
        live against a real clip:
          1. zoompan chained directly after boxblur (blurred_background's
             background blur) hung/never finished — 30s+ for an 8s clip
             that otherwise renders in ~10s. Chaining the other way
             (zoompan first, boxblur downstream of its output) doesn't
             hit this and renders at normal speed.
          2. zoompan's `s=` output size is a literal WxH, not an
             expression referencing the input's own iw/ih (unlike scale/
             crop) — it can't just target "however big blurred_background's
             foreground layer happens to be", so it needs a concrete size
             up front. Using the source's own native resolution keeps the
             zoomed output's aspect ratio identical to the original, so
             everything downstream (scale/crop/pad per crop_mode) behaves
             exactly as if it were reading the un-zoomed source.

        `on` is zoompan's cumulative output-frame counter, so on/total_frames
        maps directly to elapsed-time fraction for a given fps.
        """
        if motion_effect not in ("ken_burns_zoom", "punch_in_zoom"):
            return ""
        duration = max(0.1, clip_duration_sec)
        total_frames = max(1, round(duration * fps))
        if motion_effect == "ken_burns_zoom":
            # Slow, constant-rate zoom-in regardless of `crazy` -- this
            # effect is for calm/contemplative moments, not energy.
            max_zoom = 1.08
            zoom_expr = f"1+{max_zoom - 1:.4f}*on/{total_frames}"
        else:
            # Accelerating (quadratic) zoom-in -- crazy scales how far it
            # pushes in, for a sharper "punch" on high-energy hook types.
            max_zoom = 1.12 + 0.13 * crazy
            zoom_expr = f"1+{max_zoom - 1:.4f}*pow(on/{total_frames}\\,2)"
        return (
            f"[0:v]zoompan=z='{zoom_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d=1:s={source_w}x{source_h}:fps={fps:.0f}[zoomed];"
        )

    def _build_motion_filter(self, motion_effect: MotionEffect, crazy: float) -> str:
        """
        Non-zoom camera treatments, chained as a plain comma-continuation
        after _build_crop_filter's output (unlike the zoom effects, which
        run as a pre-stage — see _build_zoom_prestage). Applied BEFORE the
        banner/subtitle overlays are drawn, so captions always sit locked
        in place rather than moving with the shot.
        """
        if motion_effect == "shake":
            # Small sinusoidal jitter within a fixed 6% crop margin (so the
            # window never travels out of frame); amplitude scales with
            # crazy for tense/chaotic moments. w/h are constant here (no
            # `t`), so the plain crop filter handles this fine on its own
            # (crop's x/y, unlike its w/h, are re-evaluated every frame).
            amp = 6.0 + 14.0 * crazy
            return (
                f",crop=w='iw*0.94':h='ih*0.94':"
                f"x='(iw-out_w)/2+{amp:.1f}*sin(2*PI*3.5*t)':"
                f"y='(ih-out_h)/2+{amp:.1f}*cos(2*PI*4.1*t)',scale=1080:1920"
            )
        return ""

    def _build_banner_filter(self, hook_banner_text: str, warmth: float = 0.5,
                             banner_font: Optional[str] = None) -> str:
        """
        The hook banner burned over the first frames.

        Wrapped by measurement rather than assumed to fit on one line. It
        did not: a reviewer rejected a batch of three clips because every
        title ran off both edges, at 1180, 1441 and 1452 pixels inside a
        1080 pixel frame. The banner had no wrapping at all and a fixed
        42px type size, so any title beyond roughly forty characters was
        silently cropped — and titles are written by a model that has no
        idea a pixel budget exists.

        The box grows with the text instead of staying a fixed 100px slab,
        because a two-line title inside a one-line box is the same defect
        wearing a different hat.
        """
        # A saturated accent rather than near-black. The old pair ran from
        # (10,10,15) to (90,30,10) -- values dark enough to read as a grey
        # slab over dark footage, which a reviewer called a colour choice
        # that "could be more engaging". These keep the same cool-to-warm
        # direction with enough chroma to register as a deliberate band.
        box_rgb = _lerp_rgb((22, 32, 96), (150, 28, 42), warmth)    # indigo -> crimson
        text_rgb = _lerp_rgb((255, 255, 255), (255, 238, 205), warmth)  # white -> warm white
        box_hex = _rgb_to_ffmpeg_hex(box_rgb)
        text_hex = _rgb_to_ffmpeg_hex(text_rgb)

        # Resolved once: the measuring, the sizing and the drawing all have
        # to agree about which file is being used, or the box is built for
        # one face and filled with another.
        font = text_fit.font_file(banner_font)

        # Emoji come off before the box is measured, not after: drawtext
        # renders them as hollow squares, and a box sized around glyphs
        # that will not draw is the wrong size for the text that does.
        # They stay in the YouTube title, which is metadata rather than
        # pixels — that is where every video that has actually travelled
        # on these channels carries them.
        lines, font_px = text_fit.fit_text(
            text_fit.strip_emoji(hook_banner_text),
            max_width_px=BANNER_WIDTH - 2 * BANNER_PADDING,
            font_px=BANNER_FONT_PX,
            font_path=font,
            min_font_px=BANNER_MIN_FONT_PX,
            fallback_wrap=BANNER_FALLBACK_WRAP,
        )
        if not lines:
            return ""

        line_height = int(font_px * 1.25)

        # The box is sized to the ink, not to the line boxes.
        #
        # A line box is 1.25x the point size and the glyphs fill about two
        # thirds of it, so a box sized on line boxes holds 56px of text in
        # 80px of space and the slack all collects below the words: measured
        # at 25px above and 47px below, which a reviewer read as the title
        # not being centred.
        #
        # No vertical offset is applied on top of that. drawtext's y lands
        # within a pixel of the ink top already -- measured, not assumed --
        # so shifting by the font's own bbox offset double-counts and pushes
        # the text back out the other way.
        extents = text_fit.ink_extents(lines, font, font_px, line_height)
        ink_height = extents[1] if extents else len(lines) * line_height
        box_height = ink_height + 2 * BANNER_PADDING
        first_line_y = BANNER_Y + BANNER_PADDING

        parts = [f",drawbox=x={BANNER_X}:y={BANNER_Y}:w={BANNER_WIDTH}"
                 f":h={box_height}:color={box_hex}@0.88:t=fill"]

        # One drawtext per line, each centred on its own width. A single
        # drawtext with embedded newlines left-aligns every line inside the
        # block, so a two-line title hangs its second line under the first
        # and reads as a layout fault -- which is what a reviewer called
        # "the title is misaligned".
        #
        # Centred on the box rather than the frame: the box is inset, so
        # centring on frame width leaves the text visibly off inside it.
        box_centre = BANNER_X + BANNER_WIDTH / 2
        # A concrete bold file, not drawtext's default. Left unset, ffmpeg
        # falls back to a regular weight that disappears over video -- the
        # banner had never specified one.
        face = f"fontfile={font}:" if font else ""

        for i, line in enumerate(lines):
            parts.append(
                f"drawtext={face}text='{_escape_drawtext(line)}'"
                f":fontcolor={text_hex}:fontsize={font_px}"
                f":borderw=3:bordercolor=black@0.6"
                f":x={box_centre:.0f}-text_w/2"
                f":y={first_line_y + i * line_height}")

        return ",".join(parts)

    def render_vertical_short(
        self,
        input_video_path: str | Path,
        start_time: str,
        end_time: str,
        clip_id: str,
        crop_mode: CropMode = "center_crop",
        motion_effect: MotionEffect = "none",
        caption_font: Optional[str] = None,
        color_grade: ColorGrade = "neutral",
        hook_banner_text: Optional[str] = None,
        transcript_text: Optional[str] = None,
        warmth: float = 0.5,
        crazy: float = 0.3,
        music_path: Optional[str | Path] = None,
        narration_path: Optional[str | Path] = None,
    ) -> Dict[str, Any]:
        input_video_path = Path(input_video_path)
        if not input_video_path.exists():
            raise FileNotFoundError(f"Input video not found: {input_video_path}")

        output_video_path = self.output_dir / f"{clip_id}_9x16.mp4"
        logger.info(
            f"Rendering short '{clip_id}' from {start_time} to {end_time} -> {output_video_path.name} "
            f"(warmth={warmth:.2f}, crazy={crazy:.2f}, crop_mode={crop_mode}, "
            f"motion_effect={motion_effect}, color_grade={color_grade})"
        )

        clip_start_sec = parse_time_to_seconds(start_time)
        clip_end_sec = parse_time_to_seconds(end_time)
        clip_duration_sec = max(0.1, clip_end_sec - clip_start_sec)

        if motion_effect in ("ken_burns_zoom", "punch_in_zoom"):
            # Zoom effects run as a pre-stage on the raw decoded input at
            # its own native resolution (see _build_zoom_prestage's
            # docstring for why), so crop_mode composites from the
            # already-zoomed [zoomed] stream instead of [0:v].
            source_w, source_h = get_video_dimensions(input_video_path)
            source_fps = get_video_fps(input_video_path)
            vf = self._build_zoom_prestage(motion_effect, clip_duration_sec, crazy, source_w, source_h, source_fps)
            vf += self._build_crop_filter(crop_mode, video_label="[zoomed]")
        else:
            vf = self._build_crop_filter(crop_mode)
            vf += self._build_motion_filter(motion_effect, crazy)
        vf += self._build_color_grade_filter(color_grade)

        if hook_banner_text:
            vf += self._build_banner_filter(hook_banner_text, warmth)

        if transcript_text:
            sub_path = self.output_dir / f"{clip_id}_subs.ass"
            generate_rebased_ass_subtitle_file(
                transcript_text, sub_path, clip_start_sec, clip_end_sec,
                warmth=warmth, crazy=crazy,
                caption_font=resolve_caption_font(caption_font),
            )
            # Only the forward-slash swap is needed for cross-platform paths.
            # Wrapping in single quotes already handles ffmpeg's filtergraph
            # escaping — manually escaping ':' on top of that produces a
            # literal backslash in the filename libass tries to open.
            sub_path_str = str(sub_path).replace("\\", "/")
            vf += f",ass=filename='{sub_path_str}'"

        # Fade last, over everything: the banner and captions should come up
        # with the picture rather than sitting at full strength over a black
        # frame. st is measured from the start of the trimmed clip, so the
        # out-fade is placed from the clip's own length rather than the
        # source timeline.
        clip_seconds = max(0.0, clip_end_sec - clip_start_sec)
        if clip_seconds > 4 * EDGE_FADE_SEC:
            vf += (f",fade=t=in:st=0:d={EDGE_FADE_SEC}"
                   f",fade=t=out:st={clip_seconds - EDGE_FADE_SEC:.2f}"
                   f":d={EDGE_FADE_SEC}")

        vf += "[scaled]"

        # Builds an N-way audio mix from whichever of {original source
        # audio, Mimir's narration, Bragi's score} are actually present.
        # When narration is present it's the stream actually carrying the
        # information (either there was no dialogue to begin with, or the
        # original audio measured too quiet to rely on — see
        # measure_audio_mean_volume), so the original source audio gets
        # ducked to near-ambience under it rather than staying at full
        # presence, and the score ducks further under narration than it
        # does under raw dialogue. Music inputs are looped indefinitely so
        # a ~29s Lyria clip still covers longer clips; atrim below cuts
        # every non-video stream back down to the exact clip length.
        has_music = bool(music_path) and Path(music_path).exists()
        has_narration = bool(narration_path) and Path(narration_path).exists()
        source_has_audio = has_audio_stream(input_video_path)

        extra_inputs: List[str] = []
        mix_parts: List[str] = []
        mix_labels: List[str] = []
        next_input_idx = 1

        if has_narration:
            extra_inputs += ["-i", str(narration_path)]
            mix_parts.append(
                f"[{next_input_idx}:a]atrim=0:{clip_duration_sec:.3f},asetpts=PTS-STARTPTS,volume=1.0[narr]"
            )
            mix_labels.append("[narr]")
            next_input_idx += 1

        if has_music:
            extra_inputs += ["-stream_loop", "-1", "-i", str(music_path)]
            music_vol = 0.10 if has_narration else (0.18 if source_has_audio else 0.9)
            mix_parts.append(
                f"[{next_input_idx}:a]atrim=0:{clip_duration_sec:.3f},asetpts=PTS-STARTPTS,volume={music_vol}[bragi]"
            )
            mix_labels.append("[bragi]")
            next_input_idx += 1

        if source_has_audio:
            voice_vol = 0.05 if has_narration else 1.0
            mix_parts.append(f"[0:a]volume={voice_vol}[voice]")
            mix_labels.append("[voice]")

        filter_complex = vf
        audio_map = ["-map", "0:a?"]
        if mix_labels:
            filter_complex += ";" + ";".join(mix_parts)
            if len(mix_labels) > 1:
                # duration=longest, not first: narration is a short
                # one-shot line (often just a few seconds), and it's
                # frequently the FIRST stream added — with duration=first
                # the mixed track would end when narration does, leaving
                # the rest of the clip dead silent even though music (or
                # source audio) had more to play. duration=longest instead
                # extends short streams with silence to match whichever
                # input actually runs the clip's full length.
                filter_complex += (
                    f";{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=longest:dropout_transition=2[premix]"
                )
            else:
                # Single stream — relabel directly rather than amix with
                # inputs=1, which behaves oddly on some FFmpeg builds.
                filter_complex += f";{mix_labels[0]}anull[premix]"
            # Belt-and-suspenders: pad (if every mixed stream happened to
            # be shorter than the clip, e.g. narration alone with no
            # music) then trim to exactly clip_duration_sec, so [aout]
            # always matches the video's length regardless of which
            # combination of streams fed the mix above.
            filter_complex += (
                f";[premix]apad=whole_dur={clip_duration_sec:.3f},atrim=0:{clip_duration_sec:.3f}[aout]"
            )
            audio_map = ["-map", "[aout]"]

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_time),
            "-to", str(end_time),
            "-i", str(input_video_path),
            *extra_inputs,
            "-filter_complex", filter_complex,
            "-map", "[scaled]",
            *audio_map,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_video_path),
        ]

        logger.debug(f"FFmpeg command: {' '.join(cmd)}")
        _encode_start = time.perf_counter()
        result = subprocess.run(cmd, capture_output=True, text=True)
        logger.info(f"⏱️ FFmpeg encode for '{clip_id}' took {time.perf_counter() - _encode_start:.1f}s")
        if result.returncode != 0:
            logger.error(f"FFmpeg render failed: {result.stderr}")
            raise RuntimeError(f"FFmpeg failed with code {result.returncode}: {result.stderr}")

        logger.info(f"✨ Successfully rendered vertical short: {output_video_path}")
        return {
            "output_video_path": str(output_video_path),
            "clip_id": clip_id,
            "crop_mode": crop_mode,
            "motion_effect": motion_effect,
            "color_grade": color_grade,
            "has_subtitles": bool(transcript_text),
            "has_bragi_score": has_music,
            "has_narration": has_narration,
            "warmth": warmth,
            "crazy": crazy,
            "success": True,
        }