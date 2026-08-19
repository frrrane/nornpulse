#!/usr/bin/env python3
"""
⚡ NornPulse: End-to-End Pipeline Test Runner (test_pipeline.py)
Autonomous Media Engine by Norn Labs (nornlabs.ai)

Executes and validates the full Three Norns autonomous media cycle locally:
  ᚢ Urðr (ClickHouse Analytics)
    ➔ ᚹ Verðandi (Gemini 2.0 Flash Orchestrator)
      ➔ ᛋ Skuld (FFmpeg 9:16 Vertical Video Renderer)

Usage:
  python test_pipeline.py                      # Run full automated cycle on default asset
  python test_pipeline.py --all-presets        # Run automated cycle across all presets
  python test_pipeline.py --crop-mode blurred_background  # Test blurred background canvas mode
  python test_pipeline.py --verify-only        # Validate existing rendered outputs
"""

import os
import sys
import json
import time
import shutil
import logging
import argparse
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
import unittest

from dotenv import load_dotenv

# Ensure nornpulse root is in Python module search path
PROJECT_ROOT = Path(__file__).parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv()

# Import Norn Agent Triad
from agent.urdr_analytics import UrdrAnalytics
from agent.verdandi_orchestrator import VerdandiOrchestrator, VerdandiAnalysisResult
from agent.skuld_renderer import SkuldRenderer
from utils.sample_generator import SAMPLE_TRANSCRIPTS, create_sample_16x9_video

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("nornpulse.pipeline_runner")


def probe_media_file(file_path: str | Path) -> Dict[str, Any]:
    """Inspects video stream properties via ffprobe."""
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Media file not found: {file_path}")

    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration,size,bit_rate:stream=width,height,codec_type,codec_name",
        "-of", "json",
        str(file_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(res.stdout)

    fmt = data.get("format", {})
    streams = data.get("streams", [])
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})

    w = int(v_stream.get("width", 0))
    h = int(v_stream.get("height", 0))
    duration = float(fmt.get("duration", 0.0))

    aspect_ratio = f"{w}:{h}"
    if h > 0:
        ratio = w / h
        if abs(ratio - 9 / 16) < 0.05:
            aspect_ratio = "9:16"
        elif abs(ratio - 16 / 9) < 0.05:
            aspect_ratio = "16:9"
        elif abs(ratio - 1.0) < 0.05:
            aspect_ratio = "1:1"

    return {
        "file": str(file_path),
        "filename": file_path.name,
        "size_bytes": int(fmt.get("size", file_path.stat().st_size)),
        "duration_sec": round(duration, 2),
        "width": w,
        "height": h,
        "aspect_ratio": aspect_ratio,
        "video_codec": v_stream.get("codec_name", "unknown"),
        "audio_codec": a_stream.get("codec_name", "none"),
        "has_video": bool(v_stream),
        "has_audio": bool(a_stream),
    }


class NornPulsePipelineRunner:
    """
    Automated Test Runner for the full Urðr -> Verðandi -> Skuld pipeline.
    """

    def __init__(
        self,
        output_dir: str = "output_clips",
        sample_dir: str = "sample_data",
        gemini_api_key: Optional[str] = None,
    ):
        self.output_dir = Path(output_dir)
        self.sample_dir = Path(sample_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.sample_dir.mkdir(parents=True, exist_ok=True)

        self.api_key = gemini_api_key or os.getenv("GEMINI_API_KEY")

        # Initialize the Three Norns
        logger.info("Initializing ᚢ Urðr (ClickHouse Analytics Engine)...")
        self.urdr = UrdrAnalytics()

        logger.info("Initializing ᚹ Verðandi (Gemini 2.0 Flash Orchestrator)...")
        self.verdandi = VerdandiOrchestrator(api_key=self.api_key, urdr_tool=self.urdr)

        logger.info("Initializing ᛋ Skuld (FFmpeg 9:16 Video Renderer)...")
        self.skuld = SkuldRenderer(output_dir=str(self.output_dir))

    def ensure_source_asset(self, asset_filename: str = "demo_16x9.mp4", duration: int = 60) -> Path:
        """Ensures a valid 16:9 source video exists in sample_data/."""
        target_path = self.sample_dir / asset_filename
        if not target_path.exists() or target_path.stat().st_size < 1000:
            logger.info(f"Source asset {asset_filename} missing. Synthesizing via sample generator...")
            created = create_sample_16x9_video(str(target_path), duration=duration)
            target_path = Path(created)

        probe = probe_media_file(target_path)
        logger.info(f"Source asset ready: {target_path.name} ({probe['width']}x{probe['height']} {probe['aspect_ratio']}, {probe['duration_sec']}s)")
        return target_path

    def run_full_cycle(
        self,
        video_path: Path,
        transcript_text: str,
        video_title: str = "Keynote Autonomous Media",
        topic_category: str = "tech_ai",
        target_clips: int = 2,
        crop_mode: str = "center_crop",
    ) -> Dict[str, Any]:
        """
        Executes the complete Urðr -> Verðandi -> Skuld autonomous media cycle.
        """
        cycle_start_time = time.time()
        logger.info("=" * 70)
        logger.info(f"⚡ EXECUTING NORNPULSE AUTONOMOUS PIPELINE CYCLE")
        logger.info(f"  Source Video: {video_path.name}")
        logger.info(f"  Title:        {video_title}")
        logger.info(f"  Topic:        {topic_category}")
        logger.info(f"  Crop Mode:    {crop_mode}")
        logger.info("=" * 70)

        # ---------------------------------------------------------
        # PHASE 1: ᚢ Urðr (Historical ClickHouse Retention Priors)
        # ---------------------------------------------------------
        logger.info("\n[Phase 1] ᚢ URÐR: Querying ClickHouse hook retention intelligence...")
        ch_connected = self.urdr.is_connected()
        logger.info(f"  ClickHouse Connection Status: {'🟢 LIVE (Port 8123)' if ch_connected else '🟡 IN-MEMORY CACHE'}")

        benchmarks_df = self.urdr.get_hook_type_benchmarks()
        intelligence_summary = self.urdr.get_retention_intelligence_summary()
        logger.info(f"  Retrieved {len(benchmarks_df)} hook taxonomy benchmarks.")
        logger.info(f"  Top Performing Hook: {intelligence_summary.get('top_performing_hook_type')} (Avg 3s Hold: {intelligence_summary.get('overall_avg_3s_retention')}%)")

        # ---------------------------------------------------------
        # PHASE 2: ᚹ Verðandi (Gemini 2.0 Flash Reasoning)
        # ---------------------------------------------------------
        logger.info("\n[Phase 2] ᚹ VERÐANDI: Orchestrating transcript analysis with Gemini 2.0 Flash...")
        reasoning_mode = "🟢 Gemini 2.0 Flash API" if self.verdandi.client else "🟡 Grounded Heuristic Engine"
        logger.info(f"  Reasoning Engine: {reasoning_mode}")

        analysis_result: VerdandiAnalysisResult = self.verdandi.analyze_transcript_and_decide(
            transcript_text=transcript_text,
            video_metadata={
                "title": video_title,
                "topic": topic_category,
                "source_file": str(video_path),
            },
            target_clip_count=target_clips,
        )

        logger.info(f"  Identified {len(analysis_result.clips)} high-retention 9:16 clip candidate(s).")
        for i, clip in enumerate(analysis_result.clips, 1):
            logger.info(f"    Clip #{i}: [{clip.clip_id}] {clip.start_time} ➔ {clip.end_time} ({clip.duration_seconds}s) | Hook: {clip.hook_type} | 3s Hold: {clip.predicted_3s_retention}% | Virality: {clip.virality_score}")

        # ---------------------------------------------------------
        # PHASE 3: ᛋ Skuld (FFmpeg 9:16 Vertical Video Manifestation)
        # ---------------------------------------------------------
        logger.info(f"\n[Phase 3] ᛋ SKULD: Slicing & Rendering 9:16 vertical shorts (mode: {crop_mode})...")
        rendered_outputs = []

        for clip in analysis_result.clips:
            logger.info(f"  Rendering clip '{clip.clip_id}' ({clip.start_time} - {clip.end_time})...")
            render_res = self.skuld.render_vertical_short(
                input_video_path=str(video_path),
                start_time=clip.start_time,
                end_time=clip.end_time,
                clip_id=clip.clip_id,
                crop_mode=crop_mode,
                hook_banner_text=clip.hook_title,
            )

            # Probe and validate rendered vertical short
            out_video_path = render_res["output_video_path"]
            probe_res = probe_media_file(out_video_path)

            # Assert strict 9:16 conformity
            assert probe_res["aspect_ratio"] == "9:16", f"Rendered clip is not 9:16! Dimensions: {probe_res['width']}x{probe_res['height']}"
            assert probe_res["has_video"], "Rendered clip is missing video stream!"
            assert probe_res["has_audio"], "Rendered clip is missing audio stream!"
            assert probe_res["duration_sec"] > 0, "Rendered clip duration is zero!"

            thumb_path = render_res.get("thumbnail_path")
            if thumb_path and Path(thumb_path).exists():
                thumb_valid = Path(thumb_path).stat().st_size > 0
            else:
                thumb_valid = False

            rendered_outputs.append({
                "clip_id": clip.clip_id,
                "decision": clip.model_dump(),
                "render": render_res,
                "probe": probe_res,
                "thumbnail_valid": thumb_valid,
            })
            logger.info(f"  ✅ Rendered: {Path(out_video_path).name} ({probe_res['width']}x{probe_res['height']} {probe_res['aspect_ratio']}, {probe_res['duration_sec']}s, {probe_res['size_bytes']} bytes)")

        total_elapsed = round(time.time() - cycle_start_time, 2)
        logger.info("=" * 70)
        logger.info(f"✨ FULL PIPELINE CYCLE COMPLETED IN {total_elapsed}s")
        logger.info(f"  Total Shorts Produced: {len(rendered_outputs)}")
        logger.info("=" * 70)

        report = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": "PASSED",
            "elapsed_seconds": total_elapsed,
            "crop_mode": crop_mode,
            "source_video": str(video_path),
            "clickhouse_live": ch_connected,
            "gemini_api_active": bool(self.verdandi.client),
            "intelligence_summary": intelligence_summary,
            "analysis_result": analysis_result.model_dump(),
            "rendered_shorts": rendered_outputs,
        }

        # Save test report JSON
        report_path = self.output_dir / "pipeline_test_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        logger.info(f"Pipeline test report saved to {report_path}")

        return report


class TestNornPulsePipelineUnit(unittest.TestCase):
    """
    Standard unittest test cases for integration testing / CI test runners.
    """

    def setUp(self):
        self.runner = NornPulsePipelineRunner()

    def test_urdr_clickhouse_or_fallback(self):
        """Validates that Urðr returns benchmarks regardless of ClickHouse state."""
        benchmarks = self.runner.urdr.get_hook_type_benchmarks()
        self.assertFalse(benchmarks.empty, "Urðr benchmarks dataframe should not be empty")
        self.assertIn("hook_type", benchmarks.columns)
        self.assertIn("avg_3s_retention", benchmarks.columns)

        summary = self.runner.urdr.get_retention_intelligence_summary()
        self.assertIn("top_performing_hook_type", summary)
        self.assertGreater(summary["overall_avg_3s_retention"], 0)

    def test_verdandi_reasoning(self):
        """Validates Verðandi transcript decision making."""
        transcript = SAMPLE_TRANSCRIPTS["norn_ai_keynote"]["transcript"]
        res = self.runner.verdandi.analyze_transcript_and_decide(
            transcript_text=transcript,
            video_metadata={"title": "UnitTest Video", "topic": "tech_ai"},
            target_clip_count=2
        )
        self.assertIsInstance(res, VerdandiAnalysisResult)
        self.assertGreaterEqual(len(res.clips), 1)
        for clip in res.clips:
            self.assertTrue(clip.clip_id)
            self.assertGreater(clip.duration_seconds, 0)
            self.assertGreater(clip.predicted_3s_retention, 0)

    def test_skuld_rendering_both_modes(self):
        """Validates Skuld 9:16 rendering in both center_crop and blurred_background modes."""
        source_vid = self.runner.ensure_source_asset("test_unit_source.mp4", duration=20)

        for mode in ["center_crop", "blurred_background"]:
            render_res = self.runner.skuld.render_vertical_short(
                input_video_path=str(source_vid),
                start_time="00:00",
                end_time="00:08",
                clip_id=f"test_unit_{mode}",
                crop_mode=mode,
            )
            out_file = Path(render_res["output_video_path"])
            self.assertTrue(out_file.exists())
            self.assertGreater(out_file.stat().st_size, 1000)
            probe = probe_media_file(out_file)
            self.assertEqual(probe["aspect_ratio"], "9:16")
            self.assertEqual(probe["width"], 1080)
            self.assertEqual(probe["height"], 1920)
            self.assertTrue(probe["has_video"])
            self.assertTrue(probe["has_audio"])


def main():
    parser = argparse.ArgumentParser(
        description="⚡ NornPulse: End-to-End Pipeline Test Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--asset", type=str, default="demo_16x9.mp4", help="Source 16:9 video filename in sample_data/")
    parser.add_argument("--crop-mode", type=str, choices=["center_crop", "blurred_background"], default="center_crop", help="9:16 crop mode")
    parser.add_argument("--clips", type=int, default=2, help="Number of vertical clips to extract (default: 2)")
    parser.add_argument("--all-presets", action="store_true", help="Run pipeline across all presets in SAMPLE_TRANSCRIPTS")
    parser.add_argument("--unit-test", action="store_true", help="Run unittest test suite instead of standalone runner")

    args = parser.parse_args()

    if args.unit_test:
        suite = unittest.TestLoader().loadTestsFromTestCase(TestNornPulsePipelineUnit)
        runner = unittest.TextTestRunner(verbosity=2)
        res = runner.run(suite)
        return 0 if res.wasSuccessful() else 1

    runner = NornPulsePipelineRunner()

    if args.all_presets:
        preset_keys = list(SAMPLE_TRANSCRIPTS.keys())
        logger.info(f"Running full pipeline across {len(preset_keys)} presets: {preset_keys}")
        for p_key in preset_keys:
            p_data = SAMPLE_TRANSCRIPTS[p_key]
            video_path = runner.ensure_source_asset(f"{p_key}_16x9.mp4", duration=60)
            runner.run_full_cycle(
                video_path=video_path,
                transcript_text=p_data["transcript"],
                video_title=p_data["title"],
                topic_category=p_data["category"],
                target_clips=args.clips,
                crop_mode=args.crop_mode,
            )
        print("\n🎉 Full multi-preset test cycle completed successfully!")
        return 0

    # Single asset run
    source_video = runner.ensure_source_asset(args.asset, duration=60)
    asset_fname = Path(args.asset).name
    asset_stem = Path(args.asset).stem

    # Resolve matching transcript preset
    matched_preset = None
    for k, p in SAMPLE_TRANSCRIPTS.items():
        if p.get("filename") == asset_fname or k == asset_stem or k == args.asset:
            matched_preset = p
            break

    if not matched_preset:
        fallback_key = "test_10s_standard" if "10s" in asset_fname else "norn_ai_keynote"
        matched_preset = SAMPLE_TRANSCRIPTS.get(fallback_key, {
            "title": "⚡ NornPulse Media Stream",
            "category": "tech_ai",
            "transcript": "[00:00 - 00:05] Autonomous video pipeline running...\n[00:05 - 00:10] Sub-second retention intelligence."
        })

    runner.run_full_cycle(
        video_path=source_video,
        transcript_text=matched_preset["transcript"],
        video_title=matched_preset["title"],
        topic_category=matched_preset["category"],
        target_clips=args.clips,
        crop_mode=args.crop_mode,
    )

    print("\n🎉 Urðr ➔ Verðandi ➔ Skuld Pipeline Test Passed Successfully!\n")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
