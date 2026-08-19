"""
Sample Generator Utility for NornPulse
Generates synthetic 16:9 demo videos and pre-loaded timestamped transcripts
for immediate hackathon testing without needing external video files.
"""

import os
import subprocess
import logging
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger("nornpulse.sample_generator")

SAMPLE_TRANSCRIPTS = {
    "norn_ai_keynote": {
        "title": "⚡ Norn Labs Keynote: The Autonomous Media Revolution",
        "category": "tech_ai",
        "duration_estimate": "01:30",
        "transcript": """[00:00 - 00:08] (Speaker 1): 93% of AI video startups will go bankrupt in the next 18 months. Why? Because they're building wrappers around static prompt pipelines.
[00:08 - 00:18] (Speaker 1): If your media engine can't learn from audience retention curves in sub-second latency, you are flying blind in the algorithm.
[00:18 - 00:30] (Speaker 1): That is why at Norn Labs, we engineered NornPulse—an autonomous engine powered by the three Norns: Urðr for ClickHouse retention intelligence, Verðandi for Gemini 2.0 Flash reasoning, and Skuld for real-time video manifestation.
[00:30 - 00:45] (Speaker 2): When you connect ClickHouse directly to Gemini 2.0, the LLM doesn't just guess timestamps. It calculates exact virality coefficients based on millions of past retention data points.
[00:45 - 00:58] (Speaker 2): The results are staggering: a 42% increase in 3-second hold rate and over 60% completion rate on vertical shorts across TikTok, Reels, and YouTube.
[00:58 - 01:15] (Speaker 1): Never edit a 16:9 video manually again. Stop wasting 4 hours a week cropping timelines. The future of media is autonomous, deterministic, and instantaneous.
[01:15 - 01:30] (Speaker 1): Welcome to NornPulse. Welcome to the dawn of autonomous media."""
    },
    "clickhouse_speed_podcast": {
        "title": "⚡ Real-Time Data Architecture: Why ClickHouse Crushes Video Analytics",
        "category": "data_infra",
        "duration_estimate": "01:20",
        "transcript": """[00:00 - 00:10] (Host): Stop using traditional row-based databases for video analytics. You're throwing compute down the drain.
[00:10 - 00:24] (Guest): Think of ClickHouse like an F1 engine strapped to your retention telemetry. We can aggregate two billion retention events in under 12 milliseconds.
[00:24 - 00:38] (Host): That means when Verðandi queries Urðr, the agent gets instant sub-second historical retention distribution curves before deciding where to crop.
[00:38 - 00:52] (Guest): Exactly. By combining columnar aggregation with Gemini 2.0 Flash's ultra-low latency multimodal reasoning, you get autonomous video editing that actually converts.
[00:52 - 01:10] (Host): That is why modern creators and media companies are transitioning to autonomous retention-grounded engines like NornPulse."""
    }
}


def create_sample_16x9_video(output_path: str = "sample_data/demo_16x9.mp4", duration: int = 60) -> str:
    """
    Creates a synthetic 1080p 16:9 video with animated visual elements and audio
    using FFmpeg's built-in generator filters.
    """
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    
    if out_file.exists() and out_file.stat().st_size > 10000:
        return str(out_file)

    logger.info(f"Generating synthetic 16:9 demo video ({duration}s)...")
    
    # FFmpeg command to generate test video with animated color bars, speaker mock box, and synthetic tone
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "lavfi",
        "-i", f"testsrc=duration={duration}:size=1920x1080:rate=30",
        "-f", "lavfi",
        "-i", f"sine=frequency=440:duration={duration}:sample_rate=44100",
        "-vf", (
            "drawbox=x=760:y=240:w=400:h=600:color=cyan@0.4:t=fill,"
            "drawbox=x=860:y=320:w=200:h=200:color=white@0.8:t=fill,"
            "drawtext=text='⚡ NornPulse (16\\:9 Source)':fontcolor=white:fontsize=48:x=(w-text_w)/2:y=80,"
            "drawtext=text='Norn Labs - Autonomous Media Engine':fontcolor=yellow:fontsize=32:x=(w-text_w)/2:y=150,"
            "drawtext=text='Time\\: %{pts\\:hms}':fontcolor=white:fontsize=36:x=(w-text_w)/2:y=920"
        ),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        str(out_file)
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        logger.info(f"Synthetic demo video created successfully at {out_file}")
    except Exception as e:
        logger.error(f"Error creating synthetic demo video: {e}")
        
    return str(out_file)
