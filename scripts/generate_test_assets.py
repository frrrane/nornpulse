#!/usr/bin/env python3
"""
⚡ NornPulse: Test Asset Generator (generate_test_assets.py)
Autonomous Media Engine by Norn Labs (nornlabs.ai)

Utilizes FFmpeg's built-in filter graphs (testsrc, color, drawbox, drawtext,
aevalsrc, sine) to automatically synthesize realistic 16:9 test video clips,
accompanied by timestamped transcripts and structured metadata manifests.

Presets Generated:
1. keynote_ai_revolution   (sample_data/keynote_16x9.mp4)   - Keynote Presentation
2. podcast_clickhouse_deepdive (sample_data/podcast_16x9.mp4) - Studio Podcast
3. tech_demo_screencast    (sample_data/screencast_16x9.mp4) - Live Code Screencast
4. creator_growth_breakdown (sample_data/creator_16x9.mp4)  - High-Energy Creator Video
5. demo_16x9 (sample_data/demo_16x9.mp4)                     - Canonical Demo Video
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

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("nornpulse.test_assets")


ASSET_PRESETS: Dict[str, Dict[str, Any]] = {
    "keynote_ai_revolution": {
        "filename": "keynote_16x9.mp4",
        "title": "⚡ Norn Labs Keynote: The Autonomous Media Revolution",
        "category": "tech_ai",
        "default_duration": 75,
        "description": "Executive keynote on autonomous media intelligence featuring center speaker focus, dynamic animated section banners, and spoken cadence audio.",
        "speaker": "Speaker 1 (Norn Labs CEO) & Speaker 2 (Lead Architect)",
        "transcript": """[00:00 - 00:08] (Speaker 1): 93% of AI video startups will go bankrupt in the next 18 months. Why? Because they're building wrappers around static prompt pipelines.
[00:08 - 00:18] (Speaker 1): If your media engine can't learn from audience retention curves in sub-second latency, you are flying blind in the algorithm.
[00:18 - 00:30] (Speaker 1): That is why at Norn Labs, we engineered NornPulse—an autonomous engine powered by the three Norns: Urðr for ClickHouse retention intelligence, Verðandi for Gemini 2.0 Flash reasoning, and Skuld for real-time video manifestation.
[00:30 - 00:45] (Speaker 2): When you connect ClickHouse directly to Gemini 2.0, the LLM doesn't just guess timestamps. It calculates exact virality coefficients based on millions of past retention data points.
[00:45 - 00:58] (Speaker 2): The results are staggering: a 42% increase in 3-second hold rate and over 60% completion rate on vertical shorts across TikTok, Reels, and YouTube.
[00:58 - 01:15] (Speaker 1): Never edit a 16:9 video manually again. Stop wasting 4 hours a week cropping timelines. The future of media is autonomous, deterministic, and instantaneous.""",
        "hooks": [
            {"type": "shock_stat", "range": "00:00 - 00:30", "expected_3s_retention": 94.6},
            {"type": "curiosity_gap", "range": "00:30 - 01:00", "expected_3s_retention": 92.1}
        ]
    },
    "podcast_clickhouse_deepdive": {
        "filename": "podcast_16x9.mp4",
        "title": "⚡ Real-Time Data Architecture: Why ClickHouse Crushes Video Analytics",
        "category": "data_infra",
        "default_duration": 75,
        "description": "Studio podcast interview with dual-box host/guest layout, oscillating audio frequency simulation, and real-time database metric tickers.",
        "speaker": "Host (Data Pulse) & Guest (ClickHouse Core Engineer)",
        "transcript": """[00:00 - 00:10] (Host): Stop using traditional row-based databases for video analytics. You're throwing compute down the drain.
[00:10 - 00:24] (Guest): Think of ClickHouse like an F1 engine strapped to your retention telemetry. We can aggregate two billion retention events in under 12 milliseconds.
[00:24 - 00:38] (Host): That means when Verðandi queries Urðr, the agent gets instant sub-second historical retention distribution curves before deciding where to crop.
[00:38 - 00:52] (Guest): Exactly. By combining columnar aggregation with Gemini 2.0 Flash's ultra-low latency multimodal reasoning, you get autonomous video editing that actually converts.
[00:52 - 01:15] (Host): That is why modern creators and media companies are transitioning to autonomous retention-grounded engines like NornPulse.""",
        "hooks": [
            {"type": "contrarian_claim", "range": "00:00 - 00:25", "expected_3s_retention": 89.2},
            {"type": "metaphor_analogy", "range": "00:10 - 00:45", "expected_3s_retention": 87.5}
        ]
    },
    "tech_demo_screencast": {
        "filename": "screencast_16x9.mp4",
        "title": "💻 Autonomous Code Screencast: Real-Time Sub-Second Video Trimming",
        "category": "engineering",
        "default_duration": 65,
        "description": "Live developer screencast showing code editor UI mockup, live streaming ClickHouse query logs, facecam overlay, and rhythmic electronic synth tones.",
        "speaker": "Engineer (Norn Labs Core)",
        "transcript": """[00:00 - 00:12] (Engineer): Look at what happens when we benchmark 1M queries per second on live video telemetry.
[00:12 - 00:26] (Engineer): The hidden parameter in Gemini 2.0 that changes everything is structured schema enforcement combined with ClickHouse priors.
[00:26 - 00:44] (Engineer): In less than 500 milliseconds, Urðr retrieves the 3-second drop-off curve, and Skuld renders the exact 1080x1920 crop with zero manual clipping.
[00:44 - 01:05] (Engineer): Zero human intervention. Pure agentic automation for vertical video production.""",
        "hooks": [
            {"type": "visual_disruption", "range": "00:00 - 00:26", "expected_3s_retention": 91.0},
            {"type": "curiosity_gap", "range": "00:12 - 00:45", "expected_3s_retention": 93.8}
        ]
    },
    "creator_growth_breakdown": {
        "filename": "creator_16x9.mp4",
        "title": "🔥 Viral Hook Masterclass: Why 3-Second Retention Decides Feed Success",
        "category": "growth_hacks",
        "default_duration": 60,
        "description": "High-energy creator video with vibrant cyber-neon borders, retention KPI badge animations, and energetic pulse tones.",
        "speaker": "Creator (Viral Growth Strategist)",
        "transcript": """[00:00 - 00:12] (Creator): Never publish a vertical video before applying this 3-second hook rule.
[00:12 - 00:26] (Creator): You are wasting 4 hours every week manually editing video shorts when autonomous agents can predict virality before you post.
[00:26 - 00:42] (Creator): Shock stats and curiosity gaps consistently beat all other hook types with over 93% hold rate in historical tests.
[00:42 - 01:00] (Creator): Switch to NornPulse and automate your entire short-form pipeline today.""",
        "hooks": [
            {"type": "problem_agitation", "range": "00:00 - 00:28", "expected_3s_retention": 85.3},
            {"type": "curiosity_gap", "range": "00:26 - 00:55", "expected_3s_retention": 93.8}
        ]
    }
}


def verify_ffmpeg_installed() -> bool:
    """Verifies that FFmpeg and FFprobe binaries are available in PATH."""
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def probe_video(video_path: str | Path) -> Dict[str, Any]:
    """
    Runs ffprobe to inspect video resolution, duration, codecs, and stream health.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration,size,bit_rate:stream=width,height,codec_type,codec_name,r_frame_rate",
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
        "path": str(video_path),
        "filename": video_path.name,
        "size_bytes": int(format_info.get("size", video_path.stat().st_size)),
        "duration_sec": round(duration, 2),
        "width": width,
        "height": height,
        "aspect_ratio": aspect_ratio,
        "video_codec": video_stream.get("codec_name", "unknown"),
        "audio_codec": audio_stream.get("codec_name", "none"),
        "has_video": bool(video_stream),
        "has_audio": bool(audio_stream),
        "is_valid_16x9": (aspect_ratio == "16:9" and width >= 1280),
    }


class TestAssetGenerator:
    """
    Synthesizes realistic test video clips using FFmpeg filtergraphs.
    """

    def __init__(self, output_dir: str | Path = "sample_data"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if not verify_ffmpeg_installed():
            raise RuntimeError("FFmpeg and FFprobe must be installed in system PATH.")

    def build_keynote_video(self, output_path: Path, duration: int = 75) -> Path:
        """
        Generates Keynote Presentation Video (1920x1080 @ 30fps).
        Features: Dark cyber background, center speaker box with pulsing indicator,
        animated section labels, and multi-tone cadence audio.
        """
        logger.info(f"Synthesizing Keynote Presentation video ({duration}s) -> {output_path.name}")
        
        vf = (
            "drawbox=x=0:y=0:w=1920:h=1080:color=0x080e1a:t=fill,"
            "drawbox=x=760:y=180:w=400:h=700:color=0x1e293b@0.85:t=fill,"
            "drawbox=x=760:y=180:w=400:h=700:color=0x00f2fe@0.8:t=4,"
            "drawbox=x=860:y=280:w=200:h=200:color=0x38bdf8@0.7:t=fill,"
            "drawbox=x=840:y=260:w=240:h=240:color=0x00f2fe@0.3:t=2:enable='lt(mod(n\\,30)\\,18)',"
            "drawtext=text='⚡ SPEAKER (Norn Labs)':fontcolor=0x38bdf8:fontsize=22:x=780:y=200,"
            "drawtext=text='⚡ NORN LABS KEYNOTE\\: AUTONOMOUS MEDIA':fontcolor=white:fontsize=42:x=(w-text_w)/2:y=60,"
            "drawtext=text='Urðr (ClickHouse) • Verðandi (Gemini 2.0 Flash) • Skuld (FFmpeg)':fontcolor=0x94a3b8:fontsize=24:x=(w-text_w)/2:y=115,"
            "drawtext=text='SECTION\\: [00-18s] 93\\\\% AI Startups Failure':fontcolor=0xf43f5e:fontsize=28:x=(w-text_w)/2:y=915:enable='between(t\\,0\\,18)',"
            "drawtext=text='SECTION\\: [18-35s] The Three Norns Architecture':fontcolor=0x38bdf8:fontsize=28:x=(w-text_w)/2:y=915:enable='between(t\\,18\\,35)',"
            "drawtext=text='SECTION\\: [35-55s] ClickHouse + Gemini 2.0 Telemetry':fontcolor=0xec4899:fontsize=28:x=(w-text_w)/2:y=915:enable='between(t\\,35\\,55)',"
            "drawtext=text='SECTION\\: [55-75s] 42\\\\% Retention Leap & 60\\\\% Completion':fontcolor=0x10b981:fontsize=28:x=(w-text_w)/2:y=915:enable='gte(t\\,55)',"
            "drawtext=text='TIME\\: %{pts\\:hms}':fontcolor=white:fontsize=32:x=(w-text_w)/2:y=970,"
            "drawtext=text='NornPulse v1.0 • 1080p Widescreen [16\\:9]':fontcolor=0x64748b:fontsize=20:x=40:y=1040"
        )

        af = f"aevalsrc=sin(2*PI*440*t)*0.15*gt(mod(t\\,3)\\,0.4)+sin(2*PI*554.37*t)*0.1*gt(mod(t+1.5\\,3)\\,0.5):s=44100:d={duration}"

        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=0x080e1a:s=1920x1080:d={duration}:r=30",
            "-f", "lavfi", "-i", af,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(output_path)
        ]
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        return output_path

    def build_podcast_video(self, output_path: Path, duration: int = 75) -> Path:
        """
        Generates Podcast Video (1920x1080 @ 30fps).
        Features: Dual-box studio layout (Host vs Guest), lower-third podcast banner,
        alternating speech audio, and timecode.
        """
        logger.info(f"Synthesizing Podcast Interview video ({duration}s) -> {output_path.name}")
        
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
            # Center separator line
            "drawbox=x=958:y=220:w=4:h=600:color=0x334155:t=fill,"
            # Top Banner
            "drawtext=text='🎙️ DATA PULSE #42\\: WHY CLICKHOUSE CRUSHES VIDEO ANALYTICS':fontcolor=white:fontsize=38:x=(w-text_w)/2:y=60,"
            "drawtext=text='Columnar Aggregation Meets Gemini 2.0 Flash Agentic Reasoning':fontcolor=0x94a3b8:fontsize=24:x=(w-text_w)/2:y=115,"
            # Dynamic topic indicator
            "drawtext=text='TOPIC\\: Stop using row-based DBs for video telemetry':fontcolor=0xf59e0b:fontsize=26:x=(w-text_w)/2:y=860:enable='between(t\\,0\\,25)',"
            "drawtext=text='TOPIC\\: 2 Billion Events Aggregated in 12ms':fontcolor=0x10b981:fontsize=26:x=(w-text_w)/2:y=860:enable='between(t\\,25\\,55)',"
            "drawtext=text='TOPIC\\: Autonomous Retention-Grounded Production':fontcolor=0x00f2fe:fontsize=26:x=(w-text_w)/2:y=860:enable='gte(t\\,55)',"
            "drawtext=text='TIME\\: %{pts\\:hms}':fontcolor=white:fontsize=32:x=(w-text_w)/2:y=920,"
            "drawtext=text='Live Stream Telemetry • NornPulse Autonomous Media':fontcolor=0x64748b:fontsize=20:x=40:y=1040"
        )

        af = f"aevalsrc=sin(2*PI*330*t)*0.15*gt(mod(t\\,4)\\,2)+sin(2*PI*493.88*t)*0.15*lt(mod(t\\,4)\\,2):s=44100:d={duration}"

        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=0x0f172a:s=1920x1080:d={duration}:r=30",
            "-f", "lavfi", "-i", af,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(output_path)
        ]
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        return output_path

    def build_screencast_video(self, output_path: Path, duration: int = 65) -> Path:
        """
        Generates Screencast / Code Demo Video (1920x1080 @ 30fps).
        Features: IDE mockup window, real-time terminal telemetry logs ticker,
        mini facecam box, and synth rhythm.
        """
        logger.info(f"Synthesizing Tech Screencast video ({duration}s) -> {output_path.name}")
        
        vf = (
            "drawbox=x=0:y=0:w=1920:h=1080:color=0x0a0c10:t=fill,"
            # Main Code Editor Box
            "drawbox=x=80:y=160:w=1280:h=760:color=0x181e29@0.95:t=fill,"
            "drawbox=x=80:y=160:w=1280:h=760:color=0x38bdf8@0.6:t=3,"
            # Editor macOS style window dots
            "drawbox=x=100:y=180:w=18:h=18:color=0xef4444:t=fill,"
            "drawbox=x=130:y=180:w=18:h=18:color=0xf59e0b:t=fill,"
            "drawbox=x=160:y=180:w=18:h=18:color=0x10b981:t=fill,"
            "drawtext=text='agent/urdr_analytics.py — NornPulse Core':fontcolor=0x94a3b8:fontsize=20:x=200:y=178,"
            # Code mockup lines
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
            "drawtext=text='Port\\: 8123 (HTTP) / 9000 (TCP)':fontcolor=0x94a3b8:fontsize=18:x=1420:y=620,"
            "drawtext=text='QPS\\: 1\\,240\\,000 / sec':fontcolor=0x38bdf8:fontsize=20:x=1420:y=660,"
            "drawtext=text='Query Latency\\: 3.8 ms':fontcolor=0x10b981:fontsize=20:x=1420:y=700,"
            "drawtext=text='Avg 3s Hold\\: 94.6\\\\%':fontcolor=0xf43f5e:fontsize=20:x=1420:y=740,"
            # Top Banner
            "drawtext=text='💻 LIVE CODE BENCHMARK\\: 1M QUERIES/SEC VIDEO ENGINE':fontcolor=white:fontsize=38:x=(w-text_w)/2:y=60,"
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
            str(output_path)
        ]
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        return output_path

    def build_creator_video(self, output_path: Path, duration: int = 60) -> Path:
        """
        Generates Creator Masterclass Video (1920x1080 @ 30fps).
        Features: Cyberpunk neon framing, center presenter box, animated retention
        KPI badges, and high-impact hook alerts.
        """
        logger.info(f"Synthesizing Creator Masterclass video ({duration}s) -> {output_path.name}")
        
        vf = (
            "drawbox=x=0:y=0:w=1920:h=1080:color=0x130924:t=fill,"
            # Center Presenter Box (ideal for 9:16 extraction)
            "drawbox=x=720:y=160:w=480:h=740:color=0x261447@0.9:t=fill,"
            "drawbox=x=720:y=160:w=480:h=740:color=0xec4899@0.9:t=5,"
            "drawbox=x=840:y=300:w=240:h=240:color=0xf472b6@0.6:t=fill,"
            "drawtext=text='🔥 CREATOR FOCUS':fontcolor=0xec4899:fontsize=26:x=760:y=190,"
            # Left KPI Cards
            "drawbox=x=80:y=240:w=560:h=240:color=0x1f113a@0.9:t=fill,"
            "drawbox=x=80:y=240:w=560:h=240:color=0x00f2fe@0.7:t=3,"
            "drawtext=text='📊 3-SECOND HOLD BENCHMARK':fontcolor=0x00f2fe:fontsize=24:x=120:y=270,"
            "drawtext=text='94.6\\\\% Retention (Shock Stat)':fontcolor=white:fontsize=28:x=120:y=330,"
            "drawtext=text='ClickHouse Historical P-Value < 0.001':fontcolor=0x94a3b8:fontsize=20:x=120:y=390,"
            # Right KPI Cards
            "drawbox=x=1280:y=240:w=560:h=240:color=0x1f113a@0.9:t=fill,"
            "drawbox=x=1280:y=240:w=560:h=240:color=0x10b981@0.7:t=3,"
            "drawtext=text='⚡ TIME SAVED PER SHORT':fontcolor=0x10b981:fontsize=24:x=1320:y=270,"
            "drawtext=text='4 Hours Saved / Week':fontcolor=white:fontsize=28:x=1320:y=330,"
            "drawtext=text='Autonomous 1-Click Pipeline':fontcolor=0x94a3b8:fontsize=20:x=1320:y=390,"
            # Top Banner
            "drawtext=text='🔥 VIRAL HOOK MASTERCLASS\\: WHY 3-SECOND RETENTION WINS':fontcolor=white:fontsize=38:x=(w-text_w)/2:y=60,"
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
            str(output_path)
        ]
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        return output_path

    def generate_asset_by_name(
        self,
        preset_name: str,
        duration: Optional[int] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Generates or verifies a specific test asset by preset name.
        """
        if preset_name not in ASSET_PRESETS:
            raise KeyError(f"Unknown preset '{preset_name}'. Available: {list(ASSET_PRESETS.keys())}")

        preset = ASSET_PRESETS[preset_name]
        out_path = self.output_dir / preset["filename"]
        target_duration = duration or preset["default_duration"]

        if out_path.exists() and not force:
            logger.info(f"Asset '{out_path.name}' already exists. Probing metadata...")
            try:
                probe_res = probe_video(out_path)
                if probe_res.get("is_valid_16x9") and probe_res.get("duration_sec", 0) >= 10:
                    return {**preset, "probe": probe_res, "generated": False}
            except Exception as e:
                logger.warning(f"Existing file {out_path.name} is corrupt or unreadable ({e}). Regenerating...")

        # Build specific preset
        if preset_name == "keynote_ai_revolution":
            self.build_keynote_video(out_path, duration=target_duration)
        elif preset_name == "podcast_clickhouse_deepdive":
            self.build_podcast_video(out_path, duration=target_duration)
        elif preset_name == "tech_demo_screencast":
            self.build_screencast_video(out_path, duration=target_duration)
        elif preset_name == "creator_growth_breakdown":
            self.build_creator_video(out_path, duration=target_duration)

        probe_res = probe_video(out_path)
        logger.info(f"✨ Successfully generated {out_path.name} ({probe_res['duration_sec']}s, {probe_res['width']}x{probe_res['height']}, {probe_res['aspect_ratio']})")
        return {**preset, "probe": probe_res, "generated": True}

    def generate_all_assets(self, force: bool = False) -> Dict[str, Any]:
        """
        Generates all registered realistic test assets and builds canonical demo video.
        """
        results = {}
        for name in ASSET_PRESETS:
            results[name] = self.generate_asset_by_name(name, force=force)

        # Canonical demo_16x9.mp4 alias/copy for backward compatibility
        demo_path = self.output_dir / "demo_16x9.mp4"
        keynote_path = self.output_dir / ASSET_PRESETS["keynote_ai_revolution"]["filename"]
        if keynote_path.exists() and (not demo_path.exists() or force):
            shutil.copy2(keynote_path, demo_path)
            logger.info(f"Created canonical demo video alias: {demo_path.name}")

        test_16x9_path = self.output_dir / "test_16x9.mp4"
        podcast_path = self.output_dir / ASSET_PRESETS["podcast_clickhouse_deepdive"]["filename"]
        if podcast_path.exists() and (not test_16x9_path.exists() or force):
            shutil.copy2(podcast_path, test_16x9_path)
            logger.info(f"Created canonical test video alias: {test_16x9_path.name}")

        manifest_path = self.save_manifest()
        return {
            "assets": results,
            "manifest_path": str(manifest_path),
            "total_assets": len(results)
        }

    def save_manifest(self) -> Path:
        """
        Saves a comprehensive JSON manifest descriptor of all sample data assets.
        """
        manifest = {
            "generator": "NornPulse FFmpeg Asset Synthesizer v1.0",
            "assets_directory": str(self.output_dir.resolve()),
            "presets": {}
        }
        for key, preset in ASSET_PRESETS.items():
            video_file = self.output_dir / preset["filename"]
            probe_info = probe_video(video_file) if video_file.exists() else None
            manifest["presets"][key] = {
                "key": key,
                "title": preset["title"],
                "category": preset["category"],
                "filename": preset["filename"],
                "relative_path": f"sample_data/{preset['filename']}",
                "description": preset["description"],
                "speakers": preset["speaker"],
                "transcript": preset["transcript"],
                "hooks": preset.get("hooks", []),
                "probe": probe_info,
            }

        manifest_path = self.output_dir / "assets_manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        logger.info(f"Manifest written to {manifest_path}")
        return manifest_path


def main():
    parser = argparse.ArgumentParser(
        description="⚡ NornPulse: Autonomous FFmpeg Test Asset Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python generate_test_assets.py --all
  python generate_test_assets.py --preset keynote_ai_revolution --duration 60 --force
  python generate_test_assets.py --list
  python generate_test_assets.py --verify
        """
    )
    parser.add_argument("--all", action="store_true", help="Generate all realistic test video assets")
    parser.add_argument("--preset", type=str, choices=list(ASSET_PRESETS.keys()), help="Generate a specific preset")
    parser.add_argument("--duration", type=int, help="Override video duration in seconds")
    parser.add_argument("--output-dir", type=str, default="sample_data", help="Output directory (default: sample_data)")
    parser.add_argument("--force", action="store_true", help="Force regenerate assets even if files already exist")
    parser.add_argument("--list", action="store_true", help="List all available asset presets")
    parser.add_argument("--verify", action="store_true", help="Verify all assets in sample_data directory using ffprobe")

    args = parser.parse_args()

    if args.list:
        print("\n⚡ NornPulse Available Test Asset Presets:")
        print("=" * 75)
        for key, p in ASSET_PRESETS.items():
            print(f"• {key:30} -> {p['filename']} ({p['category']})")
            print(f"  Title: {p['title']}")
            print(f"  Desc:  {p['description']}\n")
        return 0

    generator = TestAssetGenerator(output_dir=args.output_dir)

    if args.verify:
        print("\n🔍 Verifying assets in sample_data/ via ffprobe:")
        print("=" * 75)
        for key, p in ASSET_PRESETS.items():
            p_file = Path(args.output_dir) / p["filename"]
            if p_file.exists():
                probe = probe_video(p_file)
                print(f"✅ {p_file.name:25} | {probe['duration_sec']}s | {probe['width']}x{probe['height']} ({probe['aspect_ratio']}) | v:{probe['video_codec']} a:{probe['audio_codec']}")
            else:
                print(f"❌ {p_file.name:25} | Missing from disk")
        return 0

    if args.preset:
        res = generator.generate_asset_by_name(args.preset, duration=args.duration, force=args.force)
        generator.save_manifest()
        print(f"\n✅ Asset '{args.preset}' ready at sample_data/{res['filename']}")
        return 0

    # Default to generating all assets
    logger.info("⚡ Generating full suite of realistic test video assets...")
    generator.generate_all_assets(force=args.force)
    print("\n🎉 All NornPulse test assets generated successfully in sample_data/!\n")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
