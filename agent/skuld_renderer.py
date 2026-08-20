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

import re
import subprocess
import logging
import time
from pathlib import Path
from typing import Dict, Any, Optional, Literal, Tuple

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
    Builds an ASS override-tag prefix that pops each caption in with a
    scale bounce, and adds a slight rotational wobble at higher intensity.
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


def _build_style_line(warmth: float, crazy: float) -> str:
    """Builds the ASS [V4+ Styles] line, color-graded by warmth and sized by crazy."""
    warmth = _clamp01(warmth)
    crazy = _clamp01(crazy)

    primary_rgb = _lerp_rgb((255, 255, 255), (255, 196, 84), warmth)   # white -> warm gold
    secondary_rgb = _lerp_rgb((255, 255, 0), (255, 90, 0), warmth)     # yellow -> deep orange

    primary_ass = _rgb_to_ass_color(primary_rgb)
    secondary_ass = _rgb_to_ass_color(secondary_rgb)

    fontsize = 54 + round(14 * crazy)   # 54 -> 68
    outline = 3 + round(2 * crazy)      # 3 -> 5
    shadow = 2 + round(2 * crazy)       # 2 -> 4

    return (
        f"Style: KineticViral,sans-serif,{fontsize},{primary_ass},{secondary_ass},"
        f"&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,{outline},{shadow},2,50,50,300,1"
    )


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
    subtitle file for FFmpeg.
    """
    output_ass_path = Path(output_ass_path)
    output_ass_path.parent.mkdir(parents=True, exist_ok=True)

    style_line = _build_style_line(warmth, crazy)
    kinetic_prefix = _kinetic_prefix(crazy)

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
    lines_written = 0

    for line in transcript_text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        times = re.findall(r"(?:\[)?(\d{1,2}:\d{2}(?:[:.]\d+)?)(?:\])?", line)
        if not times:
            continue

        abs_start = parse_time_to_seconds(times[0])
        abs_end = parse_time_to_seconds(times[1]) if len(times) >= 2 else abs_start + 4.0

        # Inclusive overlap check: catches any line overlapping the clip window
        if max(abs_start, clip_start_sec) >= min(abs_end, clip_end_sec):
            continue

        rel_start = max(0.0, abs_start - clip_start_sec)
        rel_end = min(clip_duration, abs_end - clip_start_sec)

        start_ass = seconds_to_ass_time(rel_start)
        end_ass = seconds_to_ass_time(rel_end)

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
        clean_text = _escape_ass_text(clean_text)

        if not clean_text:
            continue

        ass_content += f"Dialogue: 0,{start_ass},{end_ass},KineticViral,,0,0,0,,{kinetic_prefix}{clean_text}\n"
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