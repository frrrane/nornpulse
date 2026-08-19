"""
⚡ NornPulse: Standard Sample Generator Utility (utils/sample_generator.py)
Autonomous Media Engine by Norn Labs (nornlabs.ai)

Generates synthetic 16:9 test video assets (1080p @ 30fps, H.264 / AAC)
with dynamic visual overlays (timecodes, speaker framing, telemetry badges)
and rich sample transcripts formatted for autonomous retention analysis.
"""

import os
import sys
import json
import shutil
import logging
import argparse
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger("nornpulse.sample_generator")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)

# ---------------------------------------------------------------------------
# Standard Sample Transcripts & Metadata Presets
# ---------------------------------------------------------------------------
SAMPLE_TRANSCRIPTS: Dict[str, Dict[str, Any]] = {
    "test_10s_standard": {
        "title": "⚡ NornPulse Standard 10s Benchmark",
        "category": "benchmark_test",
        "duration_estimate": "00:10",
        "filename": "test_10s_standard.mp4",
        "speaker": "AI Announcer & System Telemetry",
        "transcript": (
            "[00:00 - 00:03] (System): Initializing NornPulse autonomous video ingestion pipeline.\n"
            "[00:03 - 00:07] (Urðr Engine): Aggregating sub-second retention priors and calculating virality coefficients.\n"
            "[00:07 - 00:10] (Skuld Engine): Rendering deterministic 9:16 vertical crop with active speaker centering."
        ),
        "hooks": [
            {"type": "shock_stat", "range": "00:00 - 00:03", "expected_3s_retention": 95.8},
            {"type": "curiosity_gap", "range": "00:03 - 00:07", "expected_3s_retention": 92.4}
        ]
    },
    "test_10s_keynote": {
        "title": "⚡ Norn Labs Keynote: 10s Autonomous Media Teaser",
        "category": "tech_ai",
        "duration_estimate": "00:10",
        "filename": "test_10s_keynote.mp4",
        "speaker": "Speaker 1 (Norn Labs CEO)",
        "transcript": (
            "[00:00 - 00:03] (Speaker 1): 93% of AI video startups will fail because they lack retention feedback.\n"
            "[00:03 - 00:07] (Speaker 1): NornPulse connects ClickHouse telemetry directly to Gemini 2.0 reasoning.\n"
            "[00:07 - 00:10] (Speaker 1): Never edit a 16:9 video manually again. The future is autonomous."
        ),
        "hooks": [
            {"type": "shock_stat", "range": "00:00 - 00:03", "expected_3s_retention": 94.6},
            {"type": "problem_agitation", "range": "00:03 - 00:07", "expected_3s_retention": 91.2}
        ]
    },
    "test_10s_podcast": {
        "title": "🎙️ Data Pulse 10s: ClickHouse vs Video Analytics",
        "category": "data_infra",
        "duration_estimate": "00:10",
        "filename": "test_10s_podcast.mp4",
        "speaker": "Host (Data Pulse) & Guest (ClickHouse Engineer)",
        "transcript": (
            "[00:00 - 00:04] (Host): Why are traditional databases useless for sub-second video analytics?\n"
            "[00:04 - 00:08] (Guest): ClickHouse aggregates 2 billion retention events in under 12 milliseconds.\n"
            "[00:08 - 00:10] (Host): That allows instant agentic crop decisions without human lag."
        ),
        "hooks": [
            {"type": "contrarian_claim", "range": "00:00 - 00:04", "expected_3s_retention": 89.4},
            {"type": "metaphor_analogy", "range": "00:04 - 00:08", "expected_3s_retention": 93.1}
        ]
    },
    "test_10s_screencast": {
        "title": "💻 Live Code Screencast: Sub-Second Video Trimming",
        "category": "engineering",
        "duration_estimate": "00:10",
        "filename": "test_10s_screencast.mp4",
        "speaker": "Core Engineer (Norn Labs)",
        "transcript": (
            "[00:00 - 00:03] (Engineer): Watch Urðr query 1M retention rows in 3.8 milliseconds.\n"
            "[00:03 - 00:07] (Engineer): Gemini 2.0 Flash identifies optimal hook boundaries with zero schema hallucination.\n"
            "[00:07 - 00:10] (Engineer): Skuld compiles the 9:16 vertical render in pure hardware-accelerated FFmpeg."
        ),
        "hooks": [
            {"type": "visual_disruption", "range": "00:00 - 00:03", "expected_3s_retention": 91.0},
            {"type": "curiosity_gap", "range": "00:03 - 00:07", "expected_3s_retention": 93.8}
        ]
    },
    "test_10s_creator": {
        "title": "🔥 Viral Hook Masterclass: 3-Second Rule",
        "category": "growth_hacks",
        "duration_estimate": "00:10",
        "filename": "test_10s_creator.mp4",
        "speaker": "Creator (Viral Growth Strategist)",
        "transcript": (
            "[00:00 - 00:03] (Creator): Never publish a vertical video before applying this 3-second hook rule.\n"
            "[00:03 - 00:07] (Creator): Shock stats and curiosity gaps consistently beat all other hooks by 42%.\n"
            "[00:07 - 00:10] (Creator): Switch to NornPulse and automate your entire short-form pipeline today."
        ),
        "hooks": [
            {"type": "shock_stat", "range": "00:00 - 00:03", "expected_3s_retention": 96.2},
            {"type": "curiosity_gap", "range": "00:03 - 00:07", "expected_3s_retention": 93.5}
        ]
    },
    "norn_ai_keynote": {
        "title": "⚡ Norn Labs Keynote: The Autonomous Media Revolution",
        "category": "tech_ai",
        "duration_estimate": "01:30",
        "filename": "keynote_16x9.mp4",
        "speaker": "Speaker 1 & Speaker 2",
        "transcript": (
            "[00:00 - 00:08] (Speaker 1): 93% of AI video startups will go bankrupt in the next 18 months. Why? Because they're building wrappers around static prompt pipelines.\n"
            "[00:08 - 00:18] (Speaker 1): If your media engine can't learn from audience retention curves in sub-second latency, you are flying blind in the algorithm.\n"
            "[00:18 - 00:30] (Speaker 1): That is why at Norn Labs, we engineered NornPulse—an autonomous engine powered by the three Norns: Urðr for ClickHouse retention intelligence, Verðandi for Gemini 2.0 Flash reasoning, and Skuld for real-time video manifestation.\n"
            "[00:30 - 00:45] (Speaker 2): When you connect ClickHouse directly to Gemini 2.0, the LLM doesn't just guess timestamps. It calculates exact virality coefficients based on millions of past retention data points.\n"
            "[00:45 - 00:58] (Speaker 2): The results are staggering: a 42% increase in 3-second hold rate and over 60% completion rate on vertical shorts across TikTok, Reels, and YouTube.\n"
            "[00:58 - 01:15] (Speaker 1): Never edit a 16:9 video manually again. Stop wasting 4 hours a week cropping timelines. The future of media is autonomous, deterministic, and instantaneous.\n"
            "[01:15 - 01:30] (Speaker 1): Welcome to NornPulse. Welcome to the dawn of autonomous media."
        ),
        "hooks": [
            {"type": "shock_stat", "range": "00:00 - 00:30", "expected_3s_retention": 94.6},
            {"type": "curiosity_gap", "range": "00:30 - 01:00", "expected_3s_retention": 92.1}
        ]
    },
    "clickhouse_speed_podcast": {
        "title": "⚡ Real-Time Data Architecture: Why ClickHouse Crushes Video Analytics",
        "category": "data_infra",
        "duration_estimate": "01:20",
        "filename": "podcast_16x9.mp4",
        "speaker": "Host & Guest",
        "transcript": (
            "[00:00 - 00:10] (Host): Stop using traditional row-based databases for video analytics. You're throwing compute down the drain.\n"
            "[00:10 - 00:24] (Guest): Think of ClickHouse like an F1 engine strapped to your retention telemetry. We can aggregate two billion retention events in under 12 milliseconds.\n"
            "[00:24 - 00:38] (Host): That means when Verðandi queries Urðr, the agent gets instant sub-second historical retention distribution curves before deciding where to crop.\n"
            "[00:38 - 00:52] (Guest): Exactly. By combining columnar aggregation with Gemini 2.0 Flash's ultra-low latency multimodal reasoning, you get autonomous video editing that actually converts.\n"
            "[00:52 - 01:10] (Host): That is why modern creators and media companies are transitioning to autonomous retention-grounded engines like NornPulse."
        ),
        "hooks": [
            {"type": "contrarian_claim", "range": "00:00 - 00:25", "expected_3s_retention": 89.2},
            {"type": "metaphor_analogy", "range": "00:10 - 00:45", "expected_3s_retention": 87.5}
        ]
    }
}


# ---------------------------------------------------------------------------
# FFmpeg & Media Validation Utilities
# ---------------------------------------------------------------------------
def verify_ffmpeg_installed() -> bool:
    """Verifies that ffmpeg and ffprobe binaries are available in PATH."""
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def probe_video(video_path: str | Path) -> Dict[str, Any]:
    """
    Runs ffprobe to inspect video resolution, duration, codecs, framerate, and stream health.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration,size,bit_rate:stream=width,height,codec_type,codec_name,r_frame_rate,pix_fmt,sample_rate,channels",
        "-of", "json",
        str(video_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    probe_data = json.loads(result.stdout)

    format_info = probe_data.get("format", {})
    streams = probe_data.get("streams", [])

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})

    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))
    duration = float(format_info.get("duration", 0.0))
    fps_str = video_stream.get("r_frame_rate", "30/1")
    try:
        num, den = map(int, fps_str.split("/"))
        fps = round(num / den, 2) if den != 0 else 30.0
    except Exception:
        fps = 30.0

    aspect_ratio = f"{width}:{height}"
    if height > 0:
        ratio_val = width / height
        if abs(ratio_val - 16 / 9) < 0.05:
            aspect_ratio = "16:9"
        elif abs(ratio_val - 9 / 16) < 0.05:
            aspect_ratio = "9:16"
        elif abs(ratio_val - 1.0) < 0.05:
            aspect_ratio = "1:1"

    return {
        "path": str(video_path.resolve()),
        "filename": video_path.name,
        "size_bytes": int(format_info.get("size", video_path.stat().st_size)),
        "size_mb": round(int(format_info.get("size", video_path.stat().st_size)) / (1024 * 1024), 2),
        "duration_sec": round(duration, 2),
        "width": width,
        "height": height,
        "fps": fps,
        "aspect_ratio": aspect_ratio,
        "video_codec": video_stream.get("codec_name", "unknown"),
        "pix_fmt": video_stream.get("pix_fmt", "unknown"),
        "audio_codec": audio_stream.get("codec_name", "none"),
        "sample_rate": audio_stream.get("sample_rate", "unknown"),
        "channels": audio_stream.get("channels", 0),
        "has_video": bool(video_stream),
        "has_audio": bool(audio_stream),
        "is_valid_16x9": (aspect_ratio == "16:9" and width >= 1280),
        "is_exact_1080p": (width == 1920 and height == 1080),
    }


def verify_asset(video_path: str | Path, expected_duration: float = 10.0, tolerance: float = 0.25) -> Dict[str, Any]:
    """
    Verifies that a generated video conforms strictly to NornPulse pipeline specifications.
    """
    probe = probe_video(video_path)
    passed_duration = abs(probe["duration_sec"] - expected_duration) <= tolerance
    passed_resolution = probe["width"] == 1920 and probe["height"] == 1080
    passed_aspect = probe["aspect_ratio"] == "16:9"
    passed_video_codec = probe["video_codec"] == "h264" and "yuv420p" in probe.get("pix_fmt", "")
    passed_audio_codec = probe["audio_codec"] == "aac"

    all_passed = all([passed_duration, passed_resolution, passed_aspect, passed_video_codec, passed_audio_codec])
    
    return {
        **probe,
        "validation": {
            "passed_all": all_passed,
            "passed_duration": passed_duration,
            "passed_resolution": passed_resolution,
            "passed_aspect": passed_aspect,
            "passed_video_codec": passed_video_codec,
            "passed_audio_codec": passed_audio_codec,
        }
    }


# ---------------------------------------------------------------------------
# Standard Video Asset Synthesis Methods
# ---------------------------------------------------------------------------
def create_sample_16x9_video(
    output_path: str = "sample_data/demo_16x9.mp4",
    duration: int = 60,
    title: str = "⚡ NornPulse (16:9 Source)",
    force: bool = False
) -> str:
    """
    Creates a synthetic 1080p 16:9 video with animated visual elements and audio
    using FFmpeg's built-in generator filters. Maintains full backwards compatibility with app.py.
    """
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    
    if out_file.exists() and out_file.stat().st_size > 10000 and not force:
        logger.info(f"Using existing demo video at {out_file}")
        return str(out_file)

    logger.info(f"Generating synthetic 16:9 demo video ({duration}s) -> {out_file.name}...")
    
    clean_title = title.replace(":", "\\:").replace("'", "")
    vf = (
        "drawbox=x=760:y=240:w=400:h=600:color=cyan@0.4:t=fill,"
        "drawbox=x=860:y=320:w=200:h=200:color=white@0.8:t=fill,"
        f"drawtext=text='{clean_title}':fontcolor=white:fontsize=48:x=(w-text_w)/2:y=80,"
        "drawtext=text='Norn Labs - Autonomous Media Engine':fontcolor=yellow:fontsize=32:x=(w-text_w)/2:y=150,"
        "drawtext=text='Time\\: %{pts\\:hms}':fontcolor=white:fontsize=36:x=(w-text_w)/2:y=920"
    )

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=1920x1080:rate=30",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}:sample_rate=44100",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out_file)
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        logger.info(f"Synthetic demo video created successfully at {out_file}")
    except Exception as e:
        logger.error(f"Error creating synthetic demo video: {e}")
        
    return str(out_file)


def create_10s_standard_video(output_path: str = "sample_data/test_10s_standard.mp4", force: bool = False) -> str:
    """
    Synthesizes the Standard 10-Second Benchmark Video (1920x1080 @ 30fps).
    Features: High-contrast cyber test pattern, center speaker indicator, dynamic timecode,
    3-stage animated telemetry status banner, and dual-tone synth audio.
    """
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    if out_file.exists() and out_file.stat().st_size > 10000 and not force:
        return str(out_file)

    logger.info(f"Synthesizing 10s Standard Benchmark video -> {out_file.name}")
    duration = 10

    vf = (
        "drawbox=x=0:y=0:w=1920:h=1080:color=0x0a0f1d:t=fill,"
        # Center Target Bounding Box (9:16 crop target area)
        "drawbox=x=656:y=40:w=608:h=1000:color=0x1e293b@0.8:t=fill,"
        "drawbox=x=656:y=40:w=608:h=1000:color=0x00f2fe@0.9:t=4,"
        # Speaker Framing Box
        "drawbox=x=760:y=220:w=400:h=560:color=0x0f172a@0.9:t=fill,"
        "drawbox=x=760:y=220:w=400:h=560:color=0x38bdf8@0.8:t=3,"
        "drawbox=x=860:y=340:w=200:h=200:color=0x38bdf8@0.6:t=fill,"
        "drawtext=text='⚡ NORNPULSE STANDARD 10s BENCHMARK':fontcolor=white:fontsize=40:x=(w-text_w)/2:y=80,"
        "drawtext=text='Resolution\\: 1920x1080 • 30 FPS • H.264 / AAC':fontcolor=0x94a3b8:fontsize=24:x=(w-text_w)/2:y=135,"
        "drawtext=text='FOCUS SPEAKER (Center Crop Target)':fontcolor=0x00f2fe:fontsize=22:x=780:y=245,"
        # Dynamic 3-stage topic indicator
        "drawtext=text='STAGE 1\\: [00-03s] Shock Hook & Telemetry Ingestion':fontcolor=0xf43f5e:fontsize=28:x=(w-text_w)/2:y=840:enable='between(t\\,0\\,3)',"
        "drawtext=text='STAGE 2\\: [03-07s] ClickHouse Sub-Second Virality Prior':fontcolor=0x38bdf8:fontsize=28:x=(w-text_w)/2:y=840:enable='between(t\\,3\\,7)',"
        "drawtext=text='STAGE 3\\: [07-10s] Skuld Autonomous 9\\:16 Manifestation':fontcolor=0x10b981:fontsize=28:x=(w-text_w)/2:y=840:enable='gte(t\\,7)',"
        "drawtext=text='TIME\\: %{pts\\:hms}':fontcolor=white:fontsize=32:x=(w-text_w)/2:y=910,"
        "drawtext=text='NornPulse Asset Synthesizer v1.0 • 10.0s Standard Unit':fontcolor=0x64748b:fontsize=20:x=40:y=1040"
    )

    af = f"aevalsrc=sin(2*PI*440*t)*0.15*gt(mod(t\\,2)\\,0.3)+sin(2*PI*554.37*t)*0.12*gt(mod(t+1\\,2)\\,0.5):s=44100:d={duration}"

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=0x0a0f1d:s=1920x1080:d={duration}:r=30",
        "-f", "lavfi", "-i", af,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out_file)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return str(out_file)


def create_10s_keynote_video(output_path: str = "sample_data/test_10s_keynote.mp4", force: bool = False) -> str:
    """
    Synthesizes the Keynote Presentation 10-Second Asset (1920x1080 @ 30fps).
    Features: Dark slate executive backdrop, pulsing center presenter frame,
    speech cadence tone, and 3-stage topic transitions.
    """
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    if out_file.exists() and out_file.stat().st_size > 10000 and not force:
        return str(out_file)

    logger.info(f"Synthesizing 10s Keynote Presentation video -> {out_file.name}")
    duration = 10

    vf = (
        "drawbox=x=0:y=0:w=1920:h=1080:color=0x080e1a:t=fill,"
        # Center Speaker Box
        "drawbox=x=760:y=180:w=400:h=700:color=0x1e293b@0.9:t=fill,"
        "drawbox=x=760:y=180:w=400:h=700:color=0x00f2fe@0.8:t=4,"
        "drawbox=x=860:y=280:w=200:h=200:color=0x38bdf8@0.7:t=fill,"
        "drawbox=x=840:y=260:w=240:h=240:color=0x00f2fe@0.3:t=2:enable='lt(mod(n\\,30)\\,18)',"
        "drawtext=text='⚡ SPEAKER (Norn Labs CEO)':fontcolor=0x38bdf8:fontsize=22:x=780:y=200,"
        "drawtext=text='⚡ NORN LABS KEYNOTE\\: AUTONOMOUS MEDIA':fontcolor=white:fontsize=42:x=(w-text_w)/2:y=60,"
        "drawtext=text='Urðr (ClickHouse) • Verðandi (Gemini 2.0 Flash) • Skuld (FFmpeg)':fontcolor=0x94a3b8:fontsize=24:x=(w-text_w)/2:y=115,"
        # Dynamic Section Labels
        "drawtext=text='SECTION\\: [00-03s] 93\\\\% AI Startups Failure Hook':fontcolor=0xf43f5e:fontsize=28:x=(w-text_w)/2:y=915:enable='between(t\\,0\\,3)',"
        "drawtext=text='SECTION\\: [03-07s] ClickHouse + Gemini 2.0 Reasoning':fontcolor=0x38bdf8:fontsize=28:x=(w-text_w)/2:y=915:enable='between(t\\,3\\,7)',"
        "drawtext=text='SECTION\\: [07-10s] Deterministic Autonomous Production':fontcolor=0x10b981:fontsize=28:x=(w-text_w)/2:y=915:enable='gte(t\\,7)',"
        "drawtext=text='TIME\\: %{pts\\:hms}':fontcolor=white:fontsize=32:x=(w-text_w)/2:y=970,"
        "drawtext=text='NornPulse Keynote Unit [16\\:9 1080p]':fontcolor=0x64748b:fontsize=20:x=40:y=1040"
    )

    af = f"aevalsrc=sin(2*PI*440*t)*0.15*gt(mod(t\\,2.5)\\,0.4)+sin(2*PI*554.37*t)*0.1*gt(mod(t+1.2\\,2.5)\\,0.5):s=44100:d={duration}"

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=0x080e1a:s=1920x1080:d={duration}:r=30",
        "-f", "lavfi", "-i", af,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out_file)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return str(out_file)


def create_10s_podcast_video(output_path: str = "sample_data/test_10s_podcast.mp4", force: bool = False) -> str:
    """
    Synthesizes the Podcast Studio 10-Second Asset (1920x1080 @ 30fps).
    Features: Dual-box Host vs Guest layout, active speaker highlight switching,
    topic indicator, and alternating frequency audio.
    """
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    if out_file.exists() and out_file.stat().st_size > 10000 and not force:
        return str(out_file)

    logger.info(f"Synthesizing 10s Podcast Interview video -> {out_file.name}")
    duration = 10

    vf = (
        "drawbox=x=0:y=0:w=1920:h=1080:color=0x0f172a:t=fill,"
        # Host Box (Left)
        "drawbox=x=160:y=220:w=680:h=600:color=0x1e293b@0.9:t=fill,"
        "drawbox=x=160:y=220:w=680:h=600:color=0x3b82f6@0.8:t=4,"
        "drawbox=x=420:y=380:w=160:h=160:color=0x60a5fa@0.6:t=fill,"
        "drawtext=text='🎙️ HOST (Data Pulse)':fontcolor=0x60a5fa:fontsize=28:x=200:y=250,"
        # Guest Box (Right)
        "drawbox=x=1080:y=220:w=680:h=600:color=0x1e293b@0.9:t=fill,"
        "drawbox=x=1080:y=220:w=680:h=600:color=0xec4899@0.8:t=4,"
        "drawbox=x=1340:y=380:w=160:h=160:color=0xf472b6@0.6:t=fill,"
        "drawtext=text='⚡ GUEST (ClickHouse Core)':fontcolor=0xf472b6:fontsize=28:x=1120:y=250,"
        # Center separator
        "drawbox=x=958:y=220:w=4:h=600:color=0x334155:t=fill,"
        # Top Banner
        "drawtext=text='🎙️ DATA PULSE #42\\: WHY CLICKHOUSE CRUSHES VIDEO ANALYTICS':fontcolor=white:fontsize=38:x=(w-text_w)/2:y=60,"
        "drawtext=text='Columnar Aggregation Meets Gemini 2.0 Flash Reasoning':fontcolor=0x94a3b8:fontsize=24:x=(w-text_w)/2:y=115,"
        # Dynamic topic indicator
        "drawtext=text='TOPIC\\: [00-04s] Host Opening Challenge':fontcolor=0x3b82f6:fontsize=26:x=(w-text_w)/2:y=860:enable='between(t\\,0\\,4)',"
        "drawtext=text='TOPIC\\: [04-08s] Guest 2B Events in 12ms':fontcolor=0xec4899:fontsize=26:x=(w-text_w)/2:y=860:enable='between(t\\,4\\,8)',"
        "drawtext=text='TOPIC\\: [08-10s] Sub-Second Autonomous Cropping':fontcolor=0x10b981:fontsize=26:x=(w-text_w)/2:y=860:enable='gte(t\\,8)',"
        "drawtext=text='TIME\\: %{pts\\:hms}':fontcolor=white:fontsize=32:x=(w-text_w)/2:y=920,"
        "drawtext=text='Podcast Unit • NornPulse Autonomous Media':fontcolor=0x64748b:fontsize=20:x=40:y=1040"
    )

    af = f"aevalsrc=sin(2*PI*330*t)*0.15*lt(mod(t\\,4)\\,2)+sin(2*PI*493.88*t)*0.15*gte(mod(t\\,4)\\,2):s=44100:d={duration}"

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=0x0f172a:s=1920x1080:d={duration}:r=30",
        "-f", "lavfi", "-i", af,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out_file)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return str(out_file)


def create_10s_screencast_video(output_path: str = "sample_data/test_10s_screencast.mp4", force: bool = False) -> str:
    """
    Synthesizes the Live Code Screencast 10-Second Asset (1920x1080 @ 30fps).
    Features: IDE code editor mockup, facecam box, live telemetry metrics card,
    and rhythmic developer synth audio.
    """
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    if out_file.exists() and out_file.stat().st_size > 10000 and not force:
        return str(out_file)

    logger.info(f"Synthesizing 10s Screencast video -> {out_file.name}")
    duration = 10

    vf = (
        "drawbox=x=0:y=0:w=1920:h=1080:color=0x0a0c10:t=fill,"
        # Main Code Editor Box
        "drawbox=x=80:y=160:w=1280:h=760:color=0x181e29@0.95:t=fill,"
        "drawbox=x=80:y=160:w=1280:h=760:color=0x38bdf8@0.6:t=3,"
        "drawbox=x=100:y=180:w=18:h=18:color=0xef4444:t=fill,"
        "drawbox=x=130:y=180:w=18:h=18:color=0xf59e0b:t=fill,"
        "drawbox=x=160:y=180:w=18:h=18:color=0x10b981:t=fill,"
        "drawtext=text='agent/urdr_analytics.py — NornPulse Core':fontcolor=0x94a3b8:fontsize=20:x=200:y=178,"
        "drawtext=text='def query_hook_retention(hook_category\\, min_virality=90.0)\\:':fontcolor=0x38bdf8:fontsize=24:x=120:y=240,"
        "drawtext=text='    \"\"\"Urðr queries ClickHouse for sub-second retention priors\"\"\"':fontcolor=0x64748b:fontsize=22:x=160:y=280,"
        "drawtext=text='    return client.query_df(\"SELECT * FROM video_hook_retention\")':fontcolor=0x10b981:fontsize=24:x=160:y=320,"
        "drawtext=text='class VerdandiOrchestrator\\:':fontcolor=0xec4899:fontsize=24:x=120:y=380,"
        "drawtext=text='    model = \"gemini-2.0-flash\"':fontcolor=0xfacc15:fontsize=24:x=160:y=420,"
        "drawtext=text='    schema = VerdandiAnalysisResult':fontcolor=0xfacc15:fontsize=24:x=160:y=460,"
        # Right Facecam Box
        "drawbox=x=1400:y=160:w=440:h=360:color=0x1e293b@0.9:t=fill,"
        "drawbox=x=1400:y=160:w=440:h=360:color=0x00f2fe@0.8:t=3,"
        "drawtext=text='📹 ENGINEER (Facecam)':fontcolor=0x00f2fe:fontsize=22:x=1420:y=180,"
        "drawbox=x=1530:y=260:w=180:h=180:color=0x38bdf8@0.6:t=fill,"
        # Right Telemetry Box
        "drawbox=x=1400:y=560:w=440:h=360:color=0x111827@0.95:t=fill,"
        "drawbox=x=1400:y=560:w=440:h=360:color=0x10b981@0.7:t=3,"
        "drawtext=text='⚡ LIVE CLICKHOUSE METRICS':fontcolor=0x10b981:fontsize=20:x=1420:y=580,"
        "drawtext=text='QPS\\: 1\\,240\\,000 / sec':fontcolor=0x38bdf8:fontsize=20:x=1420:y=640,"
        "drawtext=text='Query Latency\\: 3.8 ms':fontcolor=0x10b981:fontsize=20:x=1420:y=690,"
        "drawtext=text='Avg 3s Hold\\: 94.6\\\\%':fontcolor=0xf43f5e:fontsize=20:x=1420:y=740,"
        # Top Banner
        "drawtext=text='💻 LIVE CODE BENCHMARK\\: SUB-SECOND AGENTIC RENDERING':fontcolor=white:fontsize=38:x=(w-text_w)/2:y=60,"
        "drawtext=text='TIME\\: %{pts\\:hms}':fontcolor=white:fontsize=32:x=(w-text_w)/2:y=960,"
        "drawtext=text='Terminal Session • NornPulse Developer Edition':fontcolor=0x64748b:fontsize=20:x=40:y=1040"
    )

    af = f"aevalsrc=sin(2*PI*523.25*t)*0.12*gt(mod(t\\,1)\\,0.2)+sin(2*PI*261.63*t)*0.15*gt(mod(t\\,2)\\,0.5):s=44100:d={duration}"

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=0x0a0c10:s=1920x1080:d={duration}:r=30",
        "-f", "lavfi", "-i", af,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out_file)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return str(out_file)


def create_10s_creator_video(output_path: str = "sample_data/test_10s_creator.mp4", force: bool = False) -> str:
    """
    Synthesizes the Creator Masterclass 10-Second Asset (1920x1080 @ 30fps).
    Features: Cyberpunk neon framing, center presenter box, KPI cards,
    and high-impact hook alerts.
    """
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    if out_file.exists() and out_file.stat().st_size > 10000 and not force:
        return str(out_file)

    logger.info(f"Synthesizing 10s Creator Masterclass video -> {out_file.name}")
    duration = 10

    vf = (
        "drawbox=x=0:y=0:w=1920:h=1080:color=0x130924:t=fill,"
        # Center Presenter Box
        "drawbox=x=720:y=160:w=480:h=740:color=0x261447@0.9:t=fill,"
        "drawbox=x=720:y=160:w=480:h=740:color=0xec4899@0.9:t=5,"
        "drawbox=x=840:y=300:w=240:h=240:color=0xf472b6@0.6:t=fill,"
        "drawtext=text='🔥 CREATOR FOCUS':fontcolor=0xec4899:fontsize=26:x=760:y=190,"
        # Left KPI Card
        "drawbox=x=80:y=240:w=560:h=240:color=0x1f113a@0.9:t=fill,"
        "drawbox=x=80:y=240:w=560:h=240:color=0x00f2fe@0.7:t=3,"
        "drawtext=text='📊 3-SECOND HOLD BENCHMARK':fontcolor=0x00f2fe:fontsize=24:x=120:y=270,"
        "drawtext=text='94.6\\\\% Retention (Shock Stat)':fontcolor=white:fontsize=28:x=120:y=330,"
        "drawtext=text='ClickHouse Historical P < 0.001':fontcolor=0x94a3b8:fontsize=20:x=120:y=390,"
        # Right KPI Card
        "drawbox=x=1280:y=240:w=560:h=240:color=0x1f113a@0.9:t=fill,"
        "drawbox=x=1280:y=240:w=560:h=240:color=0x10b981@0.7:t=3,"
        "drawtext=text='⚡ TIME SAVED PER SHORT':fontcolor=0x10b981:fontsize=24:x=1320:y=270,"
        "drawtext=text='4 Hours Saved / Week':fontcolor=white:fontsize=28:x=1320:y=330,"
        "drawtext=text='Autonomous 1-Click Pipeline':fontcolor=0x94a3b8:fontsize=20:x=1320:y=390,"
        # Top Banner
        "drawtext=text='🔥 VIRAL HOOK MASTERCLASS\\: 3-SECOND RETENTION':fontcolor=white:fontsize=38:x=(w-text_w)/2:y=60,"
        "drawtext=text='Grounding Short Video Creation in Audience Telemetry':fontcolor=0xf472b6:fontsize=24:x=(w-text_w)/2:y=115,"
        "drawtext=text='TIME\\: %{pts\\:hms}':fontcolor=white:fontsize=32:x=(w-text_w)/2:y=930,"
        "drawtext=text='Viral Engine • Powered by Gemini 2.0 & ClickHouse':fontcolor=0x64748b:fontsize=20:x=40:y=1040"
    )

    af = f"aevalsrc=sin(2*PI*587.33*t)*0.15*gt(mod(t\\,2)\\,0.3)+sin(2*PI*440*t)*0.12*gt(mod(t\\,1)\\,0.5):s=44100:d={duration}"

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=0x130924:s=1920x1080:d={duration}:r=30",
        "-f", "lavfi", "-i", af,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out_file)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return str(out_file)


# ---------------------------------------------------------------------------
# Batch Generator & Manifest Builder
# ---------------------------------------------------------------------------
def generate_all_10s_assets(output_dir: str = "sample_data", force: bool = False) -> Dict[str, Any]:
    """
    Generates all standardized 10-second test video assets and verifies each via ffprobe.
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    generators = {
        "test_10s_standard": (create_10s_standard_video, "test_10s_standard.mp4"),
        "test_10s_keynote": (create_10s_keynote_video, "test_10s_keynote.mp4"),
        "test_10s_podcast": (create_10s_podcast_video, "test_10s_podcast.mp4"),
        "test_10s_screencast": (create_10s_screencast_video, "test_10s_screencast.mp4"),
        "test_10s_creator": (create_10s_creator_video, "test_10s_creator.mp4"),
    }

    results = {}
    for key, (func, filename) in generators.items():
        target_file = out_path / filename
        func(str(target_file), force=force)
        verification = verify_asset(target_file, expected_duration=10.0)
        results[key] = {
            "key": key,
            "filename": filename,
            "path": str(target_file),
            "verification": verification,
            "meta": SAMPLE_TRANSCRIPTS.get(key, {})
        }

    save_manifest(output_dir)
    return results


def save_manifest(output_dir: str = "sample_data") -> Path:
    """
    Saves a comprehensive JSON manifest descriptor of all sample data assets in output_dir.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "generator": "NornPulse Media Assets Generator v1.2",
        "assets_directory": str(out_dir.resolve()),
        "presets": {}
    }

    for key, item in SAMPLE_TRANSCRIPTS.items():
        fname = item.get("filename", f"{key}.mp4")
        video_file = out_dir / fname
        probe_info = probe_video(video_file) if video_file.exists() else None
        manifest["presets"][key] = {
            "key": key,
            "title": item["title"],
            "category": item["category"],
            "filename": fname,
            "relative_path": f"sample_data/{fname}",
            "speaker": item.get("speaker", "Presenter"),
            "duration_estimate": item.get("duration_estimate", "00:10"),
            "transcript": item.get("transcript", ""),
            "hooks": item.get("hooks", []),
            "probe": probe_info,
        }

    manifest_path = out_dir / "assets_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    logger.info(f"Saved asset manifest to {manifest_path}")
    return manifest_path


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="⚡ NornPulse: Media Assets & Sample Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--all-10s", action="store_true", help="Generate all standardized 10-second test video assets")
    parser.add_argument("--all", action="store_true", help="Generate all test assets (10s and full demo)")
    parser.add_argument("--verify", action="store_true", help="Verify all assets in sample_data using ffprobe")
    parser.add_argument("--force", action="store_true", help="Force regenerate assets even if files already exist")
    parser.add_argument("--output-dir", type=str, default="sample_data", help="Output directory")

    args = parser.parse_args()

    if not verify_ffmpeg_installed():
        logger.error("FFmpeg/FFprobe is not installed in system PATH.")
        sys.exit(1)

    if args.verify:
        print("\n🔍 Verifying assets in sample_data/ via ffprobe:")
        print("=" * 80)
        out_dir = Path(args.output_dir)
        for mp4 in sorted(out_dir.glob("*.mp4")):
            res = verify_asset(mp4)
            v = res["validation"]
            status = "✅ PASS" if v["passed_all"] else "⚠️ CHECK"
            print(f"{status} {mp4.name:25} | {res['duration_sec']}s | {res['width']}x{res['height']} ({res['aspect_ratio']}) | {res['fps']}fps | v:{res['video_codec']} ({res['pix_fmt']}) a:{res['audio_codec']} | {res['size_mb']}MB")
        return 0

    if args.all_10s or args.all or len(sys.argv) == 1:
        logger.info("⚡ Generating standardized 10-second video assets...")
        results = generate_all_10s_assets(output_dir=args.output_dir, force=args.force)
        
        if args.all:
            create_sample_16x9_video(output_path=os.path.join(args.output_dir, "demo_16x9.mp4"), duration=75, force=args.force)

        print("\n🎉 Standardized 10s Test Video Assets Summary:")
        print("=" * 80)
        for k, info in results.items():
            probe = info["verification"]
            val = probe["validation"]
            status = "✅ PASS" if val["passed_all"] else "⚠️ CHECK"
            print(f"{status} {info['filename']:24} | {probe['duration_sec']}s | {probe['width']}x{probe['height']} ({probe['aspect_ratio']}) | {probe['fps']}fps | {probe['video_codec']}/{probe['audio_codec']} | {probe['size_mb']}MB")
        return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
