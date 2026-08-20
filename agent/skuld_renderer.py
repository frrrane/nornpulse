# agent/skuld_renderer.py
"""
⚡ NornPulse: Skuld Video Studio Renderer (skuld_renderer.py)
Norn Labs (nornlabs.ai)

Responsible for compiling 16:9 source videos into viral 9:16 vertical shorts,
applying dynamic kinetic subtitles with relative timeline re-basing, crop
positioning, and hook title banners.
"""

import re
import subprocess
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Literal

logger = logging.getLogger("nornpulse.skuld")

CropMode = Literal["center_crop", "blurred_background"]


def parse_time_to_seconds(time_str: str) -> float:
    """Converts HH:MM:SS, MM:SS, or SS.ms into float seconds."""
    parts = time_str.replace(",", ".").split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def seconds_to_ass_time(total_seconds: float) -> str:
    """Converts total seconds into ASS time format 'H:MM:SS.cs'."""
    if total_seconds < 0:
        total_seconds = 0
    h = int(total_seconds // 3600)
    m = int((total_seconds % 3600) // 60)
    s = int(total_seconds % 60)
    cs = int(round((total_seconds - int(total_seconds)) * 100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


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


def generate_rebased_ass_subtitle_file(
    transcript_text: str,
    output_ass_path: str | Path,
    clip_start_sec: float,
    clip_end_sec: float,
) -> Path:
    """
    Parses transcript lines, rebases timestamps relative to the clip window
    starting at 0, and generates an ASS subtitle file for FFmpeg.
    """
    output_ass_path = Path(output_ass_path)
    output_ass_path.parent.mkdir(parents=True, exist_ok=True)

    ass_content = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: KineticViral,sans-serif,54,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,2,2,50,50,300,1

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

        ass_content += f"Dialogue: 0,{start_ass},{end_ass},KineticViral,,0,0,0,,{clean_text}\n"
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

    def _build_banner_filter(self, hook_banner_text: str) -> str:
        safe_text = _escape_drawtext(hook_banner_text)
        return (
            f",drawbox=x=40:y=80:w=1000:h=100:color=0x000000@0.75:t=fill,"
            f"drawtext=text='{safe_text}':fontcolor=white:fontsize=42:x=(w-text_w)/2:y=108"
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
    ) -> Dict[str, Any]:
        input_video_path = Path(input_video_path)
        if not input_video_path.exists():
            raise FileNotFoundError(f"Input video not found: {input_video_path}")

        output_video_path = self.output_dir / f"{clip_id}_9x16.mp4"
        logger.info(
            f"Rendering short '{clip_id}' from {start_time} to {end_time} -> {output_video_path.name}"
        )

        vf = self._build_crop_filter(crop_mode)

        if hook_banner_text:
            vf += self._build_banner_filter(hook_banner_text)

        if transcript_text:
            clip_start_sec = parse_time_to_seconds(start_time)
            clip_end_sec = parse_time_to_seconds(end_time)
            sub_path = self.output_dir / f"{clip_id}_subs.ass"
            generate_rebased_ass_subtitle_file(
                transcript_text, sub_path, clip_start_sec, clip_end_sec
            )
            # Only the forward-slash swap is needed for cross-platform paths.
            # Wrapping in single quotes already handles ffmpeg's filtergraph
            # escaping — manually escaping ':' on top of that produces a
            # literal backslash in the filename libass tries to open.
            sub_path_str = str(sub_path).replace("\\", "/")
            vf += f",ass=filename='{sub_path_str}'"

        vf += "[scaled]"

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_time),
            "-to", str(end_time),
            "-i", str(input_video_path),
            "-filter_complex", vf,
            "-map", "[scaled]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_video_path),
        ]

        logger.debug(f"FFmpeg command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"FFmpeg render failed: {result.stderr}")
            raise RuntimeError(f"FFmpeg failed with code {result.returncode}: {result.stderr}")

        logger.info(f"✨ Successfully rendered vertical short: {output_video_path}")
        return {
            "output_video_path": str(output_video_path),
            "clip_id": clip_id,
            "crop_mode": crop_mode,
            "has_subtitles": bool(transcript_text),
            "success": True,
        }