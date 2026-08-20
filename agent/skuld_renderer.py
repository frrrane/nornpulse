# agent/skuld_renderer.py
"""
⚡ NornPulse: Skuld Video Studio Renderer (skuld_renderer.py)
Norn Labs (nornlabs.ai)

Responsible for compiling 16:9 source videos into viral 9:16 vertical shorts,
applying dynamic kinetic subtitles with relative timeline re-basing, crop
positioning, and hook title banners. Subtitle and banner styling is driven
by two directional sliders:

  warmth (0.0-1.0): cool blue/white  ->  warm gold/orange color grade
  crazy  (0.0-1.0): subtle, static text  ->  bouncing, wobbling kinetic text
"""

import colorsys
import re
import subprocess
import logging
import time
from pathlib import Path
from typing import Dict, Any, Optional, Literal, Tuple, List

logger = logging.getLogger("nornpulse.skuld")

CropMode = Literal["center_crop", "blurred_background"]
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


def seconds_to_ass_time(total_seconds: float) -> str:
    """Converts total seconds into ASS time format 'H:MM:SS.cs'."""
    if total_seconds < 0:
        total_seconds = 0
    h = int(total_seconds // 3600)
    m = int((total_seconds % 3600) // 60)
    s = int(total_seconds % 60)
    cs = int(round((total_seconds - int(total_seconds)) * 100))
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


def _build_style_line(warmth: float, crazy: float) -> Tuple[str, str]:
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

    # Arial Black: a real heavy/display weight rather than a generic
    # "sans-serif" name left for fontconfig to resolve however the host
    # happens to have it configured. Falls back silently to whatever
    # libass picks if a host doesn't have it installed.
    style_line = (
        f"Style: KineticViral,Arial Black,{fontsize},{primary_ass},{secondary_ass},"
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

    style_line, secondary_bgr_hex = _build_style_line(warmth, crazy)
    kinetic_prefix = _kinetic_prefix(crazy)
    words_per_chunk = _words_per_chunk(crazy)

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

        rel_start = max(0.0, abs_start - clip_start_sec)
        rel_end = min(clip_duration, abs_end - clip_start_sec)

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

        chunks = _chunk_words(clean_text, words_per_chunk)
        chunk_times = _distribute_chunk_times(chunks, rel_start, rel_end)

        for chunk_text, (chunk_start, chunk_end) in zip(chunks, chunk_times):
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

    def _build_crop_filter(self, crop_mode: CropMode) -> str:
        if crop_mode == "blurred_background":
            return (
                f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,boxblur=40:10[bg];"
                f"[0:v]scale=1080:-1:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
            )
        return f"[0:v]crop=ih*9/16:ih,scale=1080:1920"

    def _build_banner_filter(self, hook_banner_text: str, warmth: float = 0.5) -> str:
        safe_text = _escape_drawtext(hook_banner_text)
        box_rgb = _lerp_rgb((10, 10, 15), (90, 30, 10), warmth)     # near-black -> warm maroon
        text_rgb = _lerp_rgb((255, 255, 255), (255, 214, 140), warmth)  # white -> warm cream
        box_hex = _rgb_to_ffmpeg_hex(box_rgb)
        text_hex = _rgb_to_ffmpeg_hex(text_rgb)
        return (
            f",drawbox=x=40:y=80:w=1000:h=100:color={box_hex}@0.75:t=fill,"
            f"drawtext=text='{safe_text}':fontcolor={text_hex}:fontsize=42:x=(w-text_w)/2:y=108"
        )

    def render_vertical_short(
        self,
        input_video_path: str | Path,
        start_time: str,
        end_time: str,
        clip_id: str,
        crop_mode: CropMode = "center_crop",
        hook_banner_text: Optional[str] = None,
        transcript_text: Optional[str] = None,
        warmth: float = 0.5,
        crazy: float = 0.3,
        music_path: Optional[str | Path] = None,
    ) -> Dict[str, Any]:
        input_video_path = Path(input_video_path)
        if not input_video_path.exists():
            raise FileNotFoundError(f"Input video not found: {input_video_path}")

        output_video_path = self.output_dir / f"{clip_id}_9x16.mp4"
        logger.info(
            f"Rendering short '{clip_id}' from {start_time} to {end_time} -> {output_video_path.name} "
            f"(warmth={warmth:.2f}, crazy={crazy:.2f})"
        )

        clip_start_sec = parse_time_to_seconds(start_time)
        clip_end_sec = parse_time_to_seconds(end_time)
        clip_duration_sec = max(0.1, clip_end_sec - clip_start_sec)

        vf = self._build_crop_filter(crop_mode)

        if hook_banner_text:
            vf += self._build_banner_filter(hook_banner_text, warmth)

        if transcript_text:
            sub_path = self.output_dir / f"{clip_id}_subs.ass"
            generate_rebased_ass_subtitle_file(
                transcript_text, sub_path, clip_start_sec, clip_end_sec,
                warmth=warmth, crazy=crazy,
            )
            # Only the forward-slash swap is needed for cross-platform paths.
            # Wrapping in single quotes already handles ffmpeg's filtergraph
            # escaping — manually escaping ':' on top of that produces a
            # literal backslash in the filename libass tries to open.
            sub_path_str = str(sub_path).replace("\\", "/")
            vf += f",ass=filename='{sub_path_str}'"

        vf += "[scaled]"

        # Bragi's Lyria score (if any) gets mixed in under the source
        # audio — ducked low so it never competes with dialogue — rather
        # than replacing it outright. Input 1 is looped indefinitely so a
        # ~29s Lyria clip still covers clips longer than that; atrim below
        # cuts it back down to the exact clip length either way.
        has_music = bool(music_path) and Path(music_path).exists()
        extra_inputs = ["-stream_loop", "-1", "-i", str(music_path)] if has_music else []
        filter_complex = vf
        audio_map = ["-map", "0:a?"]
        if has_music:
            if has_audio_stream(input_video_path):
                filter_complex += (
                    f";[0:a]volume=1.0[voice];"
                    f"[1:a]atrim=0:{clip_duration_sec:.3f},asetpts=PTS-STARTPTS,volume=0.18[bragi];"
                    f"[voice][bragi]amix=inputs=2:duration=first:dropout_transition=2[aout]"
                )
            else:
                # No source audio at all (silent b-roll) — the Lyria track
                # is the only audio, so it plays at full presence instead
                # of duck-level volume.
                filter_complex += (
                    f";[1:a]atrim=0:{clip_duration_sec:.3f},asetpts=PTS-STARTPTS,volume=0.9[aout]"
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
            "has_subtitles": bool(transcript_text),
            "has_bragi_score": has_music,
            "warmth": warmth,
            "crazy": crazy,
            "success": True,
        }