# agent/verdandi_orchestrator.py
"""
⚡ NornPulse: Verðandi Autonomous Orchestrator (google-genai SDK)
Built for Norn Labs (nornlabs.ai)
"""

import os
import json
import logging
from typing import Callable, List, Dict, Any

from google import genai
from google.genai import types
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

from agent.skuld_renderer import SkuldRenderer, parse_time_to_seconds
from agent.urdr_analytics import UrdrAnalytics

load_dotenv(override=True)
logger = logging.getLogger("nornpulse.orchestrator")


class VerdandiADK:
    """
    Orchestrates Gemini-driven clip selection and delegates rendering to
    Skuld / telemetry logging to Urðr.

    Each call to `orchestrate_generation` builds its own bound tool
    closures rather than relying on module-level globals. This matters in
    Streamlit, where one server process serves multiple concurrent
    sessions — a shared global transcript would leak between users'
    generations, and a shared global renderer state made it hard to trust
    which clip belonged to which request.
    """

    def __init__(self, project_id: str = None):
        api_key = os.getenv("GEMINI_API_KEY")
        self.client = genai.Client(api_key=api_key)
        self.project_id = project_id
        self.skuld = SkuldRenderer(output_dir="output_clips")
        self.urdr = UrdrAnalytics()

    def _make_tools(
        self, transcript_text: str, rendered_clips: List[Dict[str, Any]]
    ) -> List[Callable]:
        """Builds request-scoped tool functions closing over this call's state."""

        def tool_execute_skuld_render(
            input_video_path: str,
            start_time: str,
            end_time: str,
            clip_id: str,
            hook_banner_text: str,
            crop_mode: str = "center_crop",
            transcript_text_override: str = "",
        ) -> str:
            """
            Renders a 9:16 vertical short with FFmpeg, burning in kinetic
            subtitles derived from the full source transcript. Always pass
            a unique clip_id per clip. crop_mode must be either
            'center_crop' or 'blurred_background'. Leave
            transcript_text_override empty to automatically use the full
            source transcript for subtitle generation.
            """
            logger.info(f"Executing Skuld render for clip_id: {clip_id} ({start_time} to {end_time})")
            resolved_transcript = (
                transcript_text_override
                if transcript_text_override and len(transcript_text_override.strip()) > 20
                else transcript_text
            )
            result = self.skuld.render_vertical_short(
                input_video_path=input_video_path,
                start_time=start_time,
                end_time=end_time,
                clip_id=clip_id,
                crop_mode=crop_mode,
                hook_banner_text=hook_banner_text,
                transcript_text=resolved_transcript,
            )
            # Record ground-truth render output. This is what the UI will
            # ultimately trust, independent of whatever the model's final
            # text summary says — so a malformed closing JSON response can
            # never orphan a clip that actually rendered successfully.
            rendered_clips.append(
                {
                    "clip_id": clip_id,
                    "start_time": start_time,
                    "end_time": end_time,
                    "output_video_path": result["output_video_path"],
                    "has_subtitles": result["has_subtitles"],
                }
            )
            return json.dumps(result)

        def tool_log_urdr_telemetry(
            clip_id: str, hook_type: str, hook_text: str, virality_score: float
        ) -> str:
            """Logs generated clip telemetry metrics into the Urðr analytics repository."""
            logger.info(f"Logging Urðr telemetry for clip_id: {clip_id}")
            match = next((c for c in rendered_clips if c["clip_id"] == clip_id), None)
            start_sec = parse_time_to_seconds(match["start_time"]) if match else 0.0
            end_sec = parse_time_to_seconds(match["end_time"]) if match else 10.0
            return self.urdr.log_generated_clip(
                clip_id=clip_id,
                hook_type=hook_type,
                hook_text=hook_text,
                start_sec=start_sec,
                end_sec=end_sec,
                retention_est=88.5,
                virality_score=virality_score,
                status="staged_clip",
            )

        return [tool_execute_skuld_render, tool_log_urdr_telemetry]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def orchestrate_generation(
        self, transcript_text: str, video_path: str, target_count: int
    ) -> List[Dict[str, Any]]:
        rendered_clips: List[Dict[str, Any]] = []
        tools = self._make_tools(transcript_text, rendered_clips)

        prompt = (
            f"Analyze this transcript:\n{transcript_text}\n\n"
            f"Source Video Path: {video_path}\n"
            f"CRITICAL: The video is only 53 seconds long. Generate exactly {target_count} clips (10-15s each). "
            f"You MUST choose a start_time and end_time strictly between 00:00 and 00:45 that matches the transcript timestamps. "
            f"Execute `tool_execute_skuld_render` and `tool_log_urdr_telemetry` for each clip with these bounds. "
            f"Return a strict JSON list response with fields: clip_id, hook_title, social_caption, virality_score, start_time, end_time. "
            f"The clip_id values in your JSON response MUST exactly match the clip_id values you passed to tool_execute_skuld_render."
        )

        try:
            chat = self.client.chats.create(
                model="gemini-3.6-flash",
                config=types.GenerateContentConfig(
                    tools=tools,
                    system_instruction=(
                        "You are Verðandi. Select valid start/end times strictly within the "
                        "00:00-00:45 range. Always call tool_execute_skuld_render before "
                        "reporting a clip as generated."
                    ),
                ),
            )

            response = chat.send_message(prompt)
            text_output = response.text if response and response.text else ""
            parsed_metadata = self._parse_model_json(text_output)

        except Exception as e:
            logger.error(f"Verðandi orchestration execution failed: {e}")
            raise e

        return self._reconcile_metadata(parsed_metadata, rendered_clips)

    def _parse_model_json(self, text_output: str) -> List[Dict[str, Any]]:
        """
        Best-effort parse of the model's closing JSON summary. Failure here
        is non-fatal — `_reconcile_metadata` falls back to the render-tracked
        ground truth for anything the model's text output doesn't cover.
        """
        try:
            if "```json" in text_output:
                json_str = text_output.split("```json")[1].split("```")[0].strip()
            elif "```" in text_output:
                json_str = text_output.split("```")[1].split("```")[0].strip()
            else:
                json_str = text_output
            parsed = json.loads(json_str)
            return parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            logger.warning("Could not parse Verðandi's closing JSON summary; falling back to render records.")
            return []

    def _reconcile_metadata(
        self, parsed_metadata: List[Dict[str, Any]], rendered_clips: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Merges the model's descriptive metadata (hook_title, social_caption,
        virality_score) with the authoritative render records (clip_id,
        output_video_path), keyed by clip_id. This guarantees the UI never
        points at a file that doesn't exist. Any rendered clip missing from
        the model's JSON still surfaces with sensible defaults; any JSON
        entry that doesn't correspond to a real render is dropped.
        """
        if not rendered_clips:
            logger.error("No clips were actually rendered by Skuld this run.")
            return []

        by_id = {m.get("clip_id"): m for m in parsed_metadata if isinstance(m, dict)}
        final: List[Dict[str, Any]] = []

        for clip in rendered_clips:
            meta = by_id.get(clip["clip_id"], {})
            final.append(
                {
                    "clip_id": clip["clip_id"],
                    "start_time": clip["start_time"],
                    "end_time": clip["end_time"],
                    "output_video_path": clip["output_video_path"],
                    "has_subtitles": clip["has_subtitles"],
                    "hook_title": meta.get("hook_title", "Autonomous Core Insight"),
                    "social_caption": meta.get("social_caption", "Engineered by NornPulse"),
                    "virality_score": meta.get("virality_score", 90.0),
                }
            )

        return final