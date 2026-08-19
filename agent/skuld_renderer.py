"""
Skuld Renderer (ᛋ - Skuld / The Future)
Part of NornPulse: Autonomous Media Engine by Norn Labs (nornlabs.ai)

Skuld weaves the thread of the future—bringing autonomous clip decisions
into physical reality. This module uses FFmpeg to slice, reformat, and crop
16:9 widescreen video into high-fidelity 9:16 vertical shorts.
"""

import os
import re
import subprocess
import logging
from typing import Optional, Dict, Any, Tuple
from pathlib import Path

logger = logging.getLogger("nornpulse.skuld")


class SkuldRenderer:
    """
    Skuld: Vertical 9:16 Video Rendering & FFmpeg Engine.
    """

    def __init__(self, output_dir: str = "output_clips"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._verify_ffmpeg()

    def _verify_ffmpeg(self) -> bool:
        """Verifies that ffmpeg and ffprobe are available in the system path."""
        try:
            res = subprocess.run(["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode == 0:
                logger.info("⚡ Skuld FFmpeg engine is verified and active.")
                return True
        except FileNotFoundError:
            logger.error("❌ FFmpeg binary not found in system PATH. Please install ffmpeg.")
        return False

    @staticmethod
    def parse_time_to_seconds(time_str: str) -> float:
        """
        Parses timestamps in 'MM:SS', 'HH:MM:SS', or seconds format into float seconds.
        """
        if isinstance(time_str, (int, float)):
            return float(time_str)
            
        time_str = str(time_str).strip()
        parts = time_str.split(":")
        try:
            if len(parts) == 1:
                return float(parts[0])
            elif len(parts) == 2:
                minutes, seconds = parts
                return float(minutes) * 60 + float(seconds)
            elif len(parts) == 3:
                hours, minutes, seconds = parts
                return float(hours) * 3600 + float(minutes) * 60 + float(seconds)
        except ValueError:
            logger.warning(f"Could not parse timestamp '{time_str}', defaulting to 0.0")
        return 0.0

    def get_video_metadata(self, video_path: str) -> Dict[str, Any]:
        """
        Extracts duration, dimensions, and bitrate from video file using ffprobe.
        """
        video_path = str(video_path)
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration,size,bit_rate:stream=width,height,r_frame_rate,codec_name",
            "-of", "default=noprint_wrappers=1:nokey=0",
            video_path
        ]
        
        metadata = {
            "duration": 0.0,
            "width": 1920,
            "height": 1080,
            "aspect_ratio": "16:9",
            "codec": "h264"
        }
        
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            for line in result.stdout.strip().split("\n"):
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    if key == "duration" and val:
                        metadata["duration"] = float(val)
                    elif key == "width" and val:
                        metadata["width"] = int(val)
                    elif key == "height" and val:
                        metadata["height"] = int(val)
                    elif key == "codec_name" and val:
                        metadata["codec"] = val

            if metadata["height"] > 0:
                ratio = metadata["width"] / metadata["height"]
                metadata["aspect_ratio"] = "16:9" if ratio > 1.3 else ("9:16" if ratio < 0.8 else "1:1")
        except Exception as e:
            logger.warning(f"Failed to probe video with ffprobe: {e}")

        return metadata

    def render_vertical_short(
        self,
        input_video_path: str,
        start_time: str,
        end_time: str,
        clip_id: str = "short_clip",
        crop_mode: str = "center_crop",
        target_width: int = 1080,
        target_height: int = 1920,
        hook_banner_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Renders a 9:16 vertical short from an input 16:9 video using FFmpeg.

        Args:
            input_video_path: Path to source 16:9 video
            start_time: Start timestamp (e.g. '00:15' or '15.0')
            end_time: End timestamp (e.g. '00:45' or '45.0')
            clip_id: Output identifier
            crop_mode: 'center_crop' or 'blurred_background'
            target_width: Output width (default: 1080)
            target_height: Output height (default: 1920)
            hook_banner_text: Optional top banner text overlay

        Returns:
            Dict containing output file paths, duration, and status.
        """
        input_path = Path(input_video_path)
        if not input_path.exists():
            raise FileNotFoundError(f"Input video does not exist: {input_video_path}")

        start_sec = self.parse_time_to_seconds(start_time)
        end_sec = self.parse_time_to_seconds(end_time)
        clip_duration = max(1.0, end_sec - start_sec)

        clean_id = re.sub(r'[^a-zA-Z0-9_-]', '_', clip_id)
        output_filename = f"{clean_id}_9x16.mp4"
        output_path = self.output_dir / output_filename
        thumbnail_path = self.output_dir / f"{clean_id}_thumb.png"

        # Construct FFmpeg Filter Graph
        if crop_mode == "blurred_background":
            # Stacked blurred background with crisp centered foreground
            # [0:v] -> bg scaled & cropped to 1080x1920 + blurred
            # [0:v] -> fg scaled to fit width 1080 with aspect ratio preserved
            # overlay centered vertically
            filter_graph = (
                f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
                f"crop={target_width}:{target_height},boxblur=28:5[bg];"
                f"[0:v]scale={target_width}:-2[fg];"
                f"[bg][fg]overlay=0:(H-h)/2[v_out]"
            )
            v_map = "[v_out]"
        else:
            # Default: High-fidelity center crop to 9:16
            filter_graph = (
                f"crop=ih*(9/16):ih:(iw-ow)/2:0,"
                f"scale={target_width}:{target_height}:flags=lanczos"
            )
            v_map = None

        # Build FFmpeg Command
        cmd = [
            "ffmpeg",
            "-y",  # Overwrite output files
            "-ss", str(start_sec),
            "-i", str(input_path),
            "-t", str(clip_duration),
        ]

        if v_map:
            cmd.extend(["-filter_complex", filter_graph, "-map", v_map, "-map", "0:a?"])
        else:
            cmd.extend(["-vf", filter_graph])

        cmd.extend([
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "22",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(output_path)
        ])

        logger.info(f"⚡ Skuld rendering command: {' '.join(cmd)}")

        process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if process.returncode != 0:
            logger.error(f"FFmpeg error: {process.stderr}")
            raise RuntimeError(f"FFmpeg rendering failed: {process.stderr}")

        # Generate Thumbnail Frame
        thumb_time = max(0.5, clip_duration / 2)
        thumb_cmd = [
            "ffmpeg",
            "-y",
            "-ss", str(thumb_time),
            "-i", str(output_path),
            "-vframes", "1",
            "-q:v", "2",
            str(thumbnail_path)
        ]
        subprocess.run(thumb_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        return {
            "status": "success",
            "clip_id": clean_id,
            "output_video_path": str(output_path),
            "thumbnail_path": str(thumbnail_path) if thumbnail_path.exists() else None,
            "start_time_sec": start_sec,
            "end_time_sec": end_sec,
            "duration_sec": clip_duration,
            "crop_mode": crop_mode,
            "aspect_ratio": "9:16",
            "resolution": f"{target_width}x{target_height}"
        }
