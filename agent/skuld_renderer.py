"""
Skuld Renderer (ᛋ - Skuld / The Future)
Part of NornPulse: Autonomous Media Engine by Norn Labs (nornlabs.ai)
"""

import os
import re
import subprocess
import logging
from typing import Optional, Dict, Any
from pathlib import Path

from config import Config

logger = logging.getLogger("nornpulse.skuld")

class SkuldRenderer:
    def __init__(self, output_dir: str = "output_clips"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._verify_ffmpeg()

    def _verify_ffmpeg(self) -> bool:
        try:
            res = subprocess.run(["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return res.returncode == 0
        except FileNotFoundError:
            return False

    @staticmethod
    def parse_time_to_seconds(time_str: str) -> float:
        if isinstance(time_str, (int, float)): return float(time_str)
        parts = str(time_str).strip().split(":")
        try:
            if len(parts) == 1: return float(parts[0])
            if len(parts) == 2: return float(parts[0]) * 60 + float(parts[1])
            if len(parts) == 3: return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        except: pass
        return 0.0

    def render_vertical_short(
        self, input_video_path: str, start_time: str, end_time: str,
        clip_id: str = "short_clip", crop_mode: str = "center_crop",
        target_width: int = 1080, target_height: int = 1920,
        hook_banner_text: Optional[str] = None,
        max_duration_override: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Render a 9:16 vertical short from a source video.

        Duration is derived from ``end_time - start_time`` and then clamped:
          • lower bound : ``Config.MIN_VIDEO_DURATION_SEC``
          • upper bound : ``max_duration_override`` if provided, otherwise
                          ``Config.EFFECTIVE_MAX_DURATION_SEC``
                          (which already respects ``EXTENDED_DURATION_MODE``).

        No magic numbers are used here – all bounds come from Config.
        """
        input_path = Path(input_video_path)
        start_sec = self.parse_time_to_seconds(start_time)
        end_sec   = self.parse_time_to_seconds(end_time)

        # Derive duration from the clip window
        raw_duration = end_sec - start_sec

        # Determine the effective ceiling for this render call
        ceiling = (
            float(max_duration_override)
            if max_duration_override is not None
            else Config.EFFECTIVE_MAX_DURATION_SEC
        )

        # Clamp to [MIN, ceiling]; fall back to DEFAULT when window is degenerate
        if raw_duration <= 0:
            logger.warning(
                f"Clip '{clip_id}': end_time ({end_time}) <= start_time ({start_time}). "
                f"Falling back to DEFAULT_VIDEO_DURATION_SEC={Config.DEFAULT_VIDEO_DURATION_SEC}s."
            )
            clip_duration = Config.DEFAULT_VIDEO_DURATION_SEC
        else:
            clip_duration = max(Config.MIN_VIDEO_DURATION_SEC, min(raw_duration, ceiling))

        if clip_duration != raw_duration and raw_duration > 0:
            logger.info(
                f"Clip '{clip_id}': requested {raw_duration:.1f}s clamped to "
                f"{clip_duration:.1f}s (min={Config.MIN_VIDEO_DURATION_SEC}s, "
                f"max={ceiling}s, extended={Config.EXTENDED_DURATION_MODE})."
            )

        clean_id = re.sub(r'[^a-zA-Z0-9_-]', '_', clip_id)
        output_path = self.output_dir / f"{clean_id}_9x16.mp4"

        # Build Filter Graph without boxradius
        if crop_mode == "blurred_background":
            filter_graph = f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,crop={target_width}:{target_height},boxblur=28:5[bg];[0:v]scale={target_width}:-2[fg];[bg][fg]overlay=0:(H-h)/2"
        else:
            filter_graph = f"crop=ih*(9/16):ih:(iw-ow)/2:0,scale={target_width}:{target_height}:flags=lanczos"

        if hook_banner_text:
            safe_text = str(hook_banner_text).replace("'", "").replace(":", "\\:")
            # Strictly compatible parameters only
            text_filter = f",drawtext=text='{safe_text}':fontcolor=white:fontsize=56:box=1:boxcolor=black@0.65:boxborderw=24:x=(w-text_w)/2:y=180"
            filter_graph += text_filter

        if crop_mode == "blurred_background" or hook_banner_text:
            if "overlay" in filter_graph and not filter_graph.endswith("[v_out]"): filter_graph += "[v_out]"
            elif not filter_graph.endswith("[v_out]"): filter_graph = f"[0:v]{filter_graph}[v_out]"
            v_map = "[v_out]"
        else:
            v_map = None

        cmd = ["ffmpeg", "-y", "-ss", str(start_sec), "-i", str(input_path), "-t", str(clip_duration)]
        if v_map: cmd.extend(["-filter_complex", filter_graph, "-map", v_map, "-map", "0:a?"])
        else: cmd.extend(["-vf", filter_graph])
        
        cmd.extend(["-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(output_path)])
        
        process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if process.returncode != 0:
            raise RuntimeError(f"FFmpeg failed: {process.stderr}")

        return {
            "status": "success",
            "output_video_path": str(output_path),
            "clip_duration_sec": clip_duration,
            "extended_mode": Config.EXTENDED_DURATION_MODE,
        }