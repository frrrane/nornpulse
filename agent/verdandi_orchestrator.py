# agent/verdandi_orchestrator.py
"""
⚡ NornPulse: Verðandi Autonomous Orchestrator (google-genai SDK)
Built for Norn Labs (nornlabs.ai)
"""

import os
import json
import logging
import re
import time
from typing import Callable, List, Dict, Any, Optional, Tuple

from google import genai
from google.genai import types
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

from agent.skuld_renderer import (
    SkuldRenderer, parse_time_to_seconds, format_seconds_to_mmss, get_video_duration_seconds,
)
from agent.urdr_analytics import UrdrAnalytics
from agent.bragi_composer import BragiComposer
from agent.heimdall_visualizer import HeimdallVisualizer

load_dotenv(override=True)
logger = logging.getLogger("nornpulse.orchestrator")


def filter_transcript_by_window(transcript_text: str, window: Optional[Tuple[float, float]]) -> str:
    """
    Restricts transcript_text to lines whose start timestamp falls inside
    window (window_start_sec, window_end_sec). Returns transcript_text
    unchanged when window is None — the default, unscoped behavior.
    """
    if not window or not transcript_text:
        return transcript_text
    window_start, window_end = window
    kept = []
    for line in transcript_text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        times = re.findall(r"(?:\[)?(\d{1,2}:\d{2}(?:[:.]\d+)?)(?:\])?", line)
        if not times:
            continue
        line_start = parse_time_to_seconds(times[0])
        if window_start <= line_start <= window_end:
            kept.append(line)
    return "\n".join(kept)


class VerdandiADK:
    """
    Orchestrates Gemini-driven clip selection and delegates rendering to
    Skuld / telemetry logging to Urðr.

    Each call to `orchestrate_generation` builds its own bound tool
    closures rather than relying on module-level globals, and explicitly
    injects Urðr's ClickHouse-derived retention intelligence into the
    prompt — the "grounds decisions in Urðr's telemetry" step the
    architecture diagram describes, actually wired into the request.
    """

    def __init__(self, project_id: str = None):
        api_key = os.getenv("GEMINI_API_KEY")
        self.client = genai.Client(api_key=api_key)
        self.project_id = project_id
        self.skuld = SkuldRenderer(output_dir="output_clips")
        self.urdr = UrdrAnalytics()
        self.bragi = BragiComposer()
        self.heimdall = HeimdallVisualizer()

    def _make_tools(
        self,
        transcript_text: str,
        rendered_clips: List[Dict[str, Any]],
        warmth: float,
        crazy: float,
        retention_summary: Dict[str, Any],
        min_duration_sec: float,
        max_duration_sec: float,
        video_duration_sec: float,
        topic_focus: Optional[str] = None,
        window: Optional[Tuple[float, float]] = None,
    ) -> List[Callable]:
        """Builds request-scoped tool functions closing over this call's state."""

        ranked_hook_types = [t["hook_type"] for t in retention_summary.get("hook_taxonomies", [])]
        top_hook_type = retention_summary.get("top_performing_hook_type")
        # Index by hook_type for O(1) lookup, so per-clip telemetry doesn't
        # need a fresh ClickHouse round-trip for data we already fetched
        # once at the start of this generation run.
        benchmarks_by_hook = {t["hook_type"]: t for t in retention_summary.get("hook_taxonomies", [])}
        # Leave a small buffer so a clip never tries to seek to the exact
        # last frame of the source file, which can trip up FFmpeg seeking.
        safe_video_end_sec = max(min_duration_sec, video_duration_sec - 1.0)

        def _clamp_duration(start_time: str, end_time: str) -> tuple[str, str]:
            """
            Code-level enforcement of the duration range AND the
            user-chosen transcript window (if any), independent of
            whether the model actually followed the prompt instructions —
            so a narrowed window is a hard guarantee, not just a hint the
            model might ignore.
            """
            start_sec = parse_time_to_seconds(start_time)
            end_sec = parse_time_to_seconds(end_time)

            if window is not None:
                window_start, window_end = window
                start_sec = max(start_sec, window_start)
                end_sec = min(end_sec, window_end)

            duration = end_sec - start_sec
            if duration > max_duration_sec:
                end_sec = start_sec + max_duration_sec
            elif duration < min_duration_sec:
                end_sec = start_sec + min_duration_sec

            end_sec = min(end_sec, safe_video_end_sec)  # never exceed the actual source video's length
            if window is not None:
                end_sec = min(end_sec, window[1])

            new_start = format_seconds_to_mmss(start_sec)
            new_end = format_seconds_to_mmss(end_sec)
            if new_start != start_time or new_end != end_time:
                logger.info(f"Clamped clip duration: {start_time}-{end_time} -> {new_start}-{new_end}")
            return new_start, new_end

        def tool_execute_skuld_render(
            input_video_path: str,
            start_time: str,
            end_time: str,
            clip_id: str,
            hook_banner_text: str,
            hook_type: str,
            crop_mode: str = "center_crop",
            transcript_text_override: str = "",
        ) -> str:
            """
            Renders a 9:16 vertical short with FFmpeg, burning in kinetic
            subtitles derived from the full source transcript. Always pass
            a unique clip_id per clip. crop_mode must be either
            'center_crop' or 'blurred_background'. Leave
            transcript_text_override empty to automatically use the full
            source transcript for subtitle generation. hook_type must be
            the same value you will later pass to tool_log_urdr_telemetry
            for this clip_id — it's used here up front to ground Bragi's
            Lyria-composed background score in Urðr's
            music_virality_benchmarks for that hook type.
            """
            logger.info(f"Executing Skuld render for clip_id: {clip_id} ({start_time} to {end_time})")
            start_time, end_time = _clamp_duration(start_time, end_time)
            resolved_transcript = (
                transcript_text_override
                if transcript_text_override and len(transcript_text_override.strip()) > 20
                else transcript_text
            )

            # Ground Bragi's Lyria prompt in real ClickHouse virality data
            # for this hook_type, then compose (or reuse a cached) track.
            # A composition failure never blocks the clip — render simply
            # proceeds without music.
            music_path = None
            music_benchmark = self.urdr.get_top_music_benchmark(hook_type=hook_type, topic_category=topic_focus)
            if music_benchmark:
                music_path = self.bragi.compose_track(hook_type, music_benchmark)

            # Heimdall composes a custom cover thumbnail grounded in the
            # same music_benchmark row — the mood/genre/energy that suits
            # this hook_type acoustically is the same signal that should
            # drive its visual mood. A generation failure never blocks the
            # clip — the render simply falls back to no custom thumbnail.
            thumbnail_path = None
            if music_benchmark:
                thumbnail_path = self.heimdall.compose_thumbnail(
                    clip_id=clip_id, hook_title=hook_banner_text, music_benchmark=music_benchmark,
                    output_dir=self.skuld.output_dir,
                )

            result = self.skuld.render_vertical_short(
                input_video_path=input_video_path,
                start_time=start_time,
                end_time=end_time,
                clip_id=clip_id,
                crop_mode=crop_mode,
                hook_banner_text=hook_banner_text,
                transcript_text=resolved_transcript,
                warmth=warmth,
                crazy=crazy,
                music_path=music_path,
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
                    "has_bragi_score": result.get("has_bragi_score", False),
                    "thumbnail_path": thumbnail_path,
                    "music_genre": music_benchmark.get("genre") if music_benchmark else None,
                    "music_mood": music_benchmark.get("mood") if music_benchmark else None,
                    "crop_mode": result.get("crop_mode", "unknown"),
                }
            )
            return json.dumps(result)

        def tool_log_urdr_telemetry(
            clip_id: str, hook_type: str, hook_text: str, virality_score: float
        ) -> str:
            """
            Logs generated clip telemetry into the Urðr ClickHouse analytics
            repository, grounding the predicted 3s-hold and completion rates
            in real historical benchmarks for this hook_type rather than
            placeholder numbers — closing the retention feedback loop the
            Three Norns architecture is built around.
            """
            logger.info(f"Logging Urðr telemetry for clip_id: {clip_id}, hook_type: {hook_type}")

            match = next((c for c in rendered_clips if c["clip_id"] == clip_id), None)
            if match:
                duration_sec = parse_time_to_seconds(match["end_time"]) - parse_time_to_seconds(match["start_time"])
            else:
                logger.warning(f"No render record found for clip_id '{clip_id}'; using fallback duration.")
                duration_sec = 10.0

            benchmark = benchmarks_by_hook.get(hook_type)
            if benchmark:
                predicted_3s = benchmark["avg_3s_retention_value"]
                predicted_completion = benchmark["avg_completion_rate"]
            else:
                # hook_type wasn't in our fetched benchmarks (model chose
                # something outside the known taxonomy) — fall back to a
                # single ClickHouse lookup only in this edge case.
                benchmark_df = self.urdr.query_hook_retention(hook_category=hook_type, limit=1)
                if not benchmark_df.empty:
                    predicted_3s = float(benchmark_df.iloc[0]["avg_3s_retention_pct"])
                    predicted_completion = float(benchmark_df.iloc[0]["completion_rate_pct"])
                else:
                    predicted_3s = float(retention_summary.get("overall_avg_3s_retention", 85.0))
                    predicted_completion = 55.0

            # --- Grounding alignment: did the model's hook_type choice
            # actually reflect Urðr's benchmark ranking, or ignore it? ---
            hook_rank: Optional[int] = (
                ranked_hook_types.index(hook_type) + 1 if hook_type in ranked_hook_types else None
            )
            is_top_tier = hook_rank is not None and hook_rank <= 2

            if match is not None:
                match["hook_type"] = hook_type
                match["hook_rank"] = hook_rank
                match["is_top_tier_hook"] = is_top_tier
                match["grounded_top_hook_type"] = top_hook_type

            success = self.urdr.log_generated_clip(
                clip_id=clip_id,
                hook_type=hook_type,
                hook_text=hook_text,
                duration_sec=duration_sec,
                predicted_3s=predicted_3s,
                predicted_completion=predicted_completion,
                virality_score=virality_score,
                topic_category="generated_clip",
                crop_mode=match.get("crop_mode", "unknown") if match else "unknown",
            )
            return json.dumps({"logged": success, "clip_id": clip_id, "hook_rank": hook_rank, "is_top_tier": is_top_tier})

        return [tool_execute_skuld_render, tool_log_urdr_telemetry]

    def _build_prompt(
        self, transcript_text: str, video_path: str, target_count: int, retention_summary: Dict[str, Any],
        min_duration_sec: float, max_duration_sec: float, video_duration_sec: float,
        vision_mode: bool = False, target_duration_sec: Optional[float] = None,
        window: Optional[Tuple[float, float]] = None,
    ) -> str:
        grounding_json = json.dumps(retention_summary, indent=2)
        topic_focus = retention_summary.get("topic_focus")
        if topic_focus:
            topic_instruction = (
                f"The grounding data above is scoped specifically to the topic_category "
                f"'{topic_focus}' — reason within that focus.\n\n"
            )
        elif retention_summary.get("topic_focus_had_no_history"):
            topic_instruction = (
                "Note: the requested topic focus had no matching ClickHouse history, so the "
                "grounding data above spans all topic categories instead.\n\n"
            )
        else:
            topic_instruction = ""

        safe_video_end = max(min_duration_sec, video_duration_sec - 1.0)
        safe_video_end_mmss = format_seconds_to_mmss(safe_video_end)
        video_duration_mmss = format_seconds_to_mmss(video_duration_sec)

        # No transcript (silent/instrumental/no-dialogue source): Verðandi
        # reasons directly over the attached video (uploaded via the Gemini
        # Files API, see _upload_video_for_vision) instead of transcript
        # text — timestamps come from what it actually sees/hears in the
        # video rather than from transcript-anchored cues.
        if vision_mode:
            content_instruction = (
                "This source has no transcript — there is no spoken dialogue to analyze, or none could "
                "be extracted. The full video is attached directly above this prompt. Watch it and choose "
                "clip windows based on what you actually see and hear: cuts, motion, on-screen text, "
                "color/lighting changes, sound design, musical beats/drops — whatever makes a moment "
                "visually or sonically striking on its own, with no dialogue required. Favor hook_type "
                "values that don't presuppose spoken narration (e.g. visual_disruption) unless another "
                "type genuinely fits what's on screen."
            )
        else:
            content_instruction = f"Analyze this transcript:\n{transcript_text}"

        window_instruction = ""
        if window is not None:
            window_start_mmss = format_seconds_to_mmss(window[0])
            window_end_mmss = format_seconds_to_mmss(window[1])
            window_instruction = (
                f"The user has manually restricted this generation to the "
                f"{window_start_mmss}-{window_end_mmss} portion of the video only — every clip's "
                f"start_time and end_time MUST fall strictly within that range, not just within "
                f"the full video length. "
            )

        duration_bias_instruction = (
            f"Within this range, lean toward whichever end is closer to your chosen hook_type's own "
            f"optimal_duration_sec, "
        )
        if target_duration_sec is not None:
            duration_bias_instruction = (
                f"Within this range, the user has set a target duration of ~{target_duration_sec:.0f}s via "
                f"the Cut Energy dial — treat that as the primary target, and only lean toward your chosen "
                f"hook_type's own optimal_duration_sec as a tiebreaker when it's close to that target, "
            )

        return (
            f"Historical Urðr ClickHouse retention intelligence — ground your hook_type "
            f"selection in this real data, don't ignore it:\n{grounding_json}\n\n"
            f"{topic_instruction}"
            f"{content_instruction}\n\n"
            f"Source Video Path: {video_path}\n"
            f"CRITICAL: The video is {video_duration_mmss} (MM:SS) long. Generate exactly {target_count} clips. "
            f"{window_instruction}"
            f"You MUST choose a start_time and end_time strictly between 00:00 and {safe_video_end_mmss}"
            + (" that matches the transcript timestamps. " if not vision_mode else " based on what you observe directly in the attached video. ")
            + f"For each clip, select a hook_type from the hook_taxonomies list above that genuinely fits the "
            f"content. Prefer hook types with higher avg_virality_score when the content honestly "
            f"supports that framing — do not force a mismatched hook type merely to chase a higher score. "
            f"HARD CONSTRAINT: every clip's duration (end_time minus start_time) MUST be between "
            f"{min_duration_sec:.0f} and {max_duration_sec:.0f} seconds — this is a strict user-set range that "
            f"overrides the taxonomy's optimal_duration_sec values whenever they'd fall outside it. "
            f"{duration_bias_instruction}"
            f"but never exceed {max_duration_sec:.0f}s or go below {min_duration_sec:.0f}s regardless of what "
            f"the historical optimum says. "
            f"Decide the clip's hook_type BEFORE calling tool_execute_skuld_render, and pass that exact same "
            f"hook_type value to both tool_execute_skuld_render and tool_log_urdr_telemetry for each clip — "
            f"tool_execute_skuld_render uses it to ground Bragi's Lyria-composed background score, so it must "
            f"never be a placeholder. "
            f"Return a strict JSON list response with fields: clip_id, hook_type, hook_title, social_caption, "
            f"virality_score, start_time, end_time. "
            f"The clip_id values in your JSON response MUST exactly match the clip_id values you passed to tool_execute_skuld_render."
        )

    def _upload_video_for_vision(self, video_path: str, timeout_sec: float = 120.0) -> types.File:
        """
        Uploads the source video to Gemini's Files API and blocks until it's
        ACTIVE (processed and ready to reason over) or the timeout elapses.
        Only called in vision mode (no usable transcript) — the text-only
        path never pays this upload/processing latency.
        """
        logger.info(f"No transcript available; uploading '{video_path}' to Gemini Files API for vision mode.")
        file_obj = self.client.files.upload(file=video_path)

        _t0 = time.perf_counter()
        while file_obj.state and file_obj.state.name == "PROCESSING":
            if time.perf_counter() - _t0 > timeout_sec:
                raise RuntimeError(
                    f"Gemini Files API did not finish processing '{video_path}' within {timeout_sec:.0f}s."
                )
            time.sleep(2)
            file_obj = self.client.files.get(name=file_obj.name)

        if file_obj.state and file_obj.state.name == "FAILED":
            raise RuntimeError(f"Gemini Files API failed to process '{video_path}': {file_obj.state}")

        logger.info(
            f"⏱️ Video upload + processing for vision mode took {time.perf_counter() - _t0:.1f}s "
            f"({file_obj.mime_type}, state={file_obj.state})"
        )
        return file_obj

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def orchestrate_generation(
        self,
        transcript_text: str,
        video_path: str,
        target_count: int,
        warmth: float = 0.5,
        crazy: float = 0.3,
        topic_focus: Optional[str] = None,
        min_duration_sec: float = 8.0,
        max_duration_sec: float = 15.0,
        cut_energy: float = 0.5,
        transcript_window: Optional[Tuple[float, float]] = None,
    ) -> List[Dict[str, Any]]:
        rendered_clips: List[Dict[str, Any]] = []

        # Detect the actual source video length instead of assuming the
        # ~53s demo asset — this is what makes the pipeline work with any
        # video, not just the one it was originally built against.
        try:
            video_duration_sec = get_video_duration_seconds(video_path)
        except Exception as e:
            logger.warning(f"Could not detect video duration via ffprobe ({e}); falling back to 53.0s assumption.")
            video_duration_sec = 53.0

        # A manually-narrowed transcript window is a hard constraint,
        # enforced twice: here (so the model's own context only contains
        # lines it's allowed to pick from) and again in _clamp_duration
        # (so even a model that ignores this still can't render outside
        # it). transcript_text is filtered before vision_mode is decided,
        # so narrowing a window down to a silent stretch correctly falls
        # back to vision mode for that stretch specifically.
        transcript_text = filter_transcript_by_window(transcript_text, transcript_window)

        # Cut Energy: where in [min_duration_sec, max_duration_sec] the
        # *target* duration should land — calm (0.0) biases toward the
        # longer end, energetic (1.0) toward the shorter end. This is a
        # prompt-level bias only (see _build_prompt's duration_bias_instruction);
        # the hard min/max range itself is still enforced unconditionally
        # in _clamp_duration regardless of cut_energy.
        cut_energy = max(0.0, min(1.0, cut_energy))
        target_duration_sec = max_duration_sec - (max_duration_sec - min_duration_sec) * cut_energy

        # Pull real ClickHouse-grounded retention intelligence BEFORE
        # prompting, so the model reasons over it rather than guessing.
        # Optionally scoped to a single topic_category the user selected.
        _t0 = time.perf_counter()
        retention_summary = self.urdr.get_retention_intelligence_summary(topic_category=topic_focus)
        logger.info(f"⏱️ Retention summary fetch took {time.perf_counter() - _t0:.1f}s")

        # No usable transcript (silent/instrumental source, or extraction
        # failed/was skipped) -> fall back to vision mode: Gemini reasons
        # directly over the uploaded video instead of transcript text.
        vision_mode = not transcript_text or not transcript_text.strip()
        video_file = self._upload_video_for_vision(video_path) if vision_mode else None

        tools = self._make_tools(
            transcript_text, rendered_clips, warmth, crazy, retention_summary,
            min_duration_sec, max_duration_sec, video_duration_sec,
            topic_focus=topic_focus, window=transcript_window,
        )
        prompt = self._build_prompt(
            transcript_text, video_path, target_count, retention_summary,
            min_duration_sec, max_duration_sec, video_duration_sec,
            vision_mode=vision_mode, target_duration_sec=target_duration_sec,
            window=transcript_window,
        )
        safe_video_end_mmss = format_seconds_to_mmss(max(min_duration_sec, video_duration_sec - 1.0))

        try:
            chat = self.client.chats.create(
                model="gemini-3.6-flash",
                config=types.GenerateContentConfig(
                    tools=tools,
                    system_instruction=(
                        f"You are Verðandi. Select valid start/end times strictly within the "
                        f"00:00-{safe_video_end_mmss} range for this video. Ground every hook_type "
                        f"selection in the Urðr retention intelligence provided in the prompt — prefer "
                        f"higher-virality hook types "
                        "when the source content genuinely fits that framing, rather than "
                        "defaulting to the same hook type regardless of content. Always call "
                        "tool_execute_skuld_render and tool_log_urdr_telemetry with matching "
                        "hook_type values before reporting a clip as generated."
                    ),
                ),
            )

            # This single call contains the ENTIRE reasoning loop: Gemini's
            # thinking, every tool_execute_skuld_render call (which includes
            # the FFmpeg encode — timed separately above), and every
            # tool_log_urdr_telemetry call (ClickHouse writes). This number
            # can't be broken down further from outside the SDK, but
            # comparing it against the sum of the FFmpeg encode times above
            # tells you how much is Gemini's own reasoning/latency vs. the
            # actual rendering work.
            _t1 = time.perf_counter()
            message = [video_file, prompt] if vision_mode else prompt
            response = chat.send_message(message)
            logger.info(f"⏱️ Gemini reasoning + all tool calls took {time.perf_counter() - _t1:.1f}s total")
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
        virality_score) with the authoritative render + telemetry records
        (clip_id, output_video_path, hook_type, grounding alignment), keyed
        by clip_id. This guarantees the UI never points at a file that
        doesn't exist, and always shows real alignment data rather than
        whatever the model's closing text happened to say.
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
                    "has_bragi_score": clip.get("has_bragi_score", False),
                    "thumbnail_path": clip.get("thumbnail_path"),
                    "music_genre": clip.get("music_genre"),
                    "music_mood": clip.get("music_mood"),
                    "hook_title": meta.get("hook_title", "Autonomous Core Insight"),
                    "social_caption": meta.get("social_caption", "Engineered by NornPulse"),
                    "virality_score": meta.get("virality_score", 90.0),
                    "hook_type": clip.get("hook_type", meta.get("hook_type", "unknown")),
                    "hook_rank": clip.get("hook_rank"),
                    "is_top_tier_hook": clip.get("is_top_tier_hook", False),
                    "grounded_top_hook_type": clip.get("grounded_top_hook_type"),
                }
            )

        return final