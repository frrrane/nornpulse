# agent/verdandi_orchestrator.py
"""
⚡ NornPulse: Verðandi Autonomous Orchestrator (google-genai SDK)
Built for Norn Labs (nornlabs.ai)
"""

import os
import json
from pathlib import Path
import logging
import random
import re
import time
from typing import Callable, List, Dict, Any, Optional, Tuple

from google import genai
from google.genai import types
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

from agent.skuld_renderer import (
    SkuldRenderer, parse_time_to_seconds, format_seconds_to_mmss, get_video_duration_seconds,
    measure_audio_mean_volume, NARRATION_FALLBACK_VOLUME_THRESHOLD_DB,
    SIDE_CROPPING_MODES,
)
from agent.urdr_analytics import UrdrAnalytics
from agent.bragi_composer import BragiComposer
from agent.heimdall_visualizer import HeimdallVisualizer
from agent.mimir_narrator import MimirNarrator
from utils.ingest import download_youtube_video, get_youtube_duration
from utils.transcribe import get_or_create_transcript

load_dotenv(override=True)
logger = logging.getLogger("nornpulse.orchestrator")

# Above this length, a video with no manually-set transcript_window gets
# one auto-picked (see orchestrate_generation's auto_window_mode) instead
# of Verðandi reasoning over the entire runtime in one call — bounds both
# the semantic "needle in a haystack" problem of picking 1-3 good moments
# out of a very long transcript, and (via the existing transcript_window
# machinery) the video/audio Gemini has to actually attend to.
AUTO_WINDOW_MAX_SEC = 600.0  # 10 minutes


def auto_window_start(video_duration_sec: float, mode: str = "random",
                      peak_sec: Optional[float] = None) -> float:
    """
    Where to open the analysis window on a video too long to reason over whole.

    Centred on the most re-watched moment when there is one. Picking at
    random and *then* telling the model to prefer cutting near the
    re-watched moments is incoherent: on a 22-minute source a random
    10-minute window discards over half the peaks before the model ever
    sees them. If the measured evidence is good enough to put in the
    prompt, it is good enough to choose the window with.

    Clamped so the window always lies inside the video, which also means a
    peak in the first or last five minutes still ends up inside it rather
    than being centred off the end.
    """
    latest = max(0.0, video_duration_sec - AUTO_WINDOW_MAX_SEC)
    if mode == "start":
        return 0.0
    if peak_sec is not None:
        return min(max(peak_sec - AUTO_WINDOW_MAX_SEC / 2, 0.0), latest)
    return random.uniform(0.0, latest)

# Batch/channel mode caps at this many videos per run, with no UI control
# to raise it — each one is a full generation run (Gemini + Lyria + image
# + TTS calls), so an uncapped "whole channel" run could mean dozens of
# those in one go.
BATCH_MAX_VIDEOS = 3

# Where source video is staged when running against Vertex, which has no
# Files API and reads from Cloud Storage instead. The bucket carries a
# short lifecycle rule: nothing in this code deletes what it uploads, and
# the Files API used to expire its own uploads after 48 hours, so without
# one every run would leave a permanent copy of its source behind.
DEFAULT_MEDIA_BUCKET = "norn-labs-pipeline-media"


def _file_digest(path: Path, chunk_size: int = 1 << 20) -> str:
    """
    A short content hash, used to name the staged copy of a video.

    Content-addressed rather than named after the file, because the
    pipeline reuses fixed local names — yt_input.mp4, batch_0_input.mp4 —
    for whatever it is working on. Keying the object on the name would let
    one run read the previous run's video; keying it on the bytes means a
    repeated run of the same source skips a fifty-megabyte upload and a
    different source can never collide with it.
    """
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()[:32]


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


def _clean_transcript_window_text(transcript_slice: str) -> str:
    """
    Strips bracketed timestamps from a filter_transcript_by_window() slice
    and joins the remaining lines into clean spoken-word prose, for
    Mímir's narration fallback — the script it reads should be the actual
    words, not raw "[00:16] text" transcript formatting.
    """
    lines = []
    for line in transcript_slice.strip().split("\n"):
        clean = re.sub(r"\[.*?\]", "", line).strip()
        if clean:
            lines.append(clean)
    return " ".join(lines)


# A cue line. Sentence ends are detected on the cue text, since a sentence
# routinely spans several cues.
#
# The fractional part is not optional decoration. The transcriber is
# instructed to emit "[MM:SS.mmm]" -- whole seconds round every caption to
# the nearest second, visibly out of sync with the speech -- and this
# pattern used to require "[MM:SS]" exactly. It therefore matched nothing at
# all, and had matched nothing since the millisecond format landed.
#
# parse_cues returning an empty list is not visible as a failure: every
# caller treats "no cues" as "nothing to do". snap_to_sentences, which
# exists because reviewers rejected clips for starting mid-thought and
# stopping mid-sentence, silently became a no-op and clips went back to
# being cut on the clock. The hour component is accepted too, for a source
# long enough to need one.
_CUE_RE = re.compile(
    r"^\s*\[(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?\]\s*(.*)$")
_SENTENCE_END = re.compile(r"[.!?][\"\')\]]*\s*$")


def parse_cues(transcript_text: str) -> List[Tuple[float, str]]:
    """
    Timestamped cues as (seconds, text), in order.

    Sub-second precision is kept rather than floored: the whole reason the
    transcriber emits milliseconds is that rounding to the nearest second
    is visibly out of sync with the speech, and throwing them away here
    would put the rounding back one layer down.
    """
    cues = []
    for line in (transcript_text or "").splitlines():
        m = _CUE_RE.match(line)
        if not m:
            continue
        hours, minutes, seconds, millis, text = m.groups()
        at = (int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
              + int((millis or "0").ljust(3, "0")) / 1000.0)
        cues.append((at, text.strip()))
    return cues


def snap_to_sentences(start_sec: float, end_sec: float, transcript_text: str,
                      max_shift_sec: float = 3.0) -> Tuple[float, float]:
    """
    Nudge a cut so it begins a sentence and ends one.

    Reviewers rejected clips for starting mid-thought and for stopping
    mid-sentence, which is what a purely time-based cut produces: the
    duration clamp knows how long a clip should be and nothing about where
    language begins or ends. Cue timings give sentence boundaries for free.

    Deliberately conservative. Only boundaries within max_shift_sec of the
    requested point are considered, so a clip is never dragged somewhere
    the model did not intend just to find a full stop; if nothing suitable
    is close, the original time is kept and the clip is no worse than
    before. The caller re-clamps afterwards, so duration and window limits
    still win.
    """
    cues = parse_cues(transcript_text)
    if len(cues) < 2:
        return start_sec, end_sec

    # A cue starts a sentence if the previous cue's text ended one.
    sentence_starts = [cues[0][0]]
    sentence_ends = []
    for i, (t, text) in enumerate(cues):
        if _SENTENCE_END.search(text):
            # The sentence finishes when the next cue begins; if this is the
            # last cue, allow a short tail rather than inventing a time.
            sentence_ends.append(cues[i + 1][0] if i + 1 < len(cues) else t + 3.0)
            if i + 1 < len(cues):
                sentence_starts.append(cues[i + 1][0])

    def nearest(candidates, target):
        near = [c for c in candidates if abs(c - target) <= max_shift_sec]
        return min(near, key=lambda c: abs(c - target)) if near else target

    snapped_start = nearest(sentence_starts, start_sec)
    snapped_end = nearest(sentence_ends, end_sec)
    # Never let snapping invert or collapse the range; the clamp downstream
    # assumes a sane ordering.
    if snapped_end <= snapped_start:
        return start_sec, end_sec
    return snapped_start, snapped_end


# Words that, as the first thing a viewer hears, tell them they have walked
# in halfway through. A Short has about one second to justify itself, and it
# is spent differently by "More than twenty lunar landings" than by "And so
# that means the lander will need...".
#
# Conjunctions and discourse markers are the clearest tell: they explicitly
# refer back to something the viewer did not hear.
_WEAK_FIRST_WORD = re.compile(
    r"^\s*(and|but|so|or|because|which|then|also|well|now|anyway|however|"
    r"therefore|thus|plus|although|though|since|while|whereas|meanwhile|"
    r"basically|actually|essentially|obviously)\b", re.I)

# Pronouns with no referent yet. "It weighs forty tons" is a fine sentence
# and a poor opening line, because nobody knows what "it" is.
_ORPHAN_PRONOUN = re.compile(
    r"^\s*(it|this|that|these|those|they|them|he|she|his|her|their|there)\b",
    re.I)


def clip_opening_line(transcript_text: str, start_sec: float) -> str:
    """The first thing spoken at or after the cut point."""
    for time_sec, text in parse_cues(transcript_text):
        if time_sec >= start_sec - 0.5 and text.strip():
            return text.strip()
    return ""


def weak_opening(line: str) -> Optional[str]:
    """
    Why this opening line will lose a viewer, or None if it will not.

    Deterministic, because the instruction telling the model to open
    strongly is an instruction and not a control — the same reason the
    rights check has a pattern net under its model call. A reviewer should
    see this before the clip is rendered rather than after watching it.
    """
    line = (line or "").strip()
    if not line:
        return None

    if _WEAK_FIRST_WORD.match(line):
        first = line.split()[0].rstrip(",.")
        return (f"opens on {first!r}, which refers back to something the "
                f"viewer has not heard")
    if _ORPHAN_PRONOUN.match(line):
        first = line.split()[0].rstrip(",.")
        return (f"opens on {first!r} with nothing for it to refer to yet")
    if line[:1].islower():
        return "opens mid-sentence"
    return None


def unique_clip_id(clip_id: str, output_dir: str | Path) -> str:
    """
    Make a clip id that no previous run has already used on disk.

    Clip ids come from the model, which has no memory of earlier runs and
    happily returns "clip_1" again. Every artifact is named from the id, so
    a repeat silently overwrites the previous clip's render, subtitles,
    thumbnail and metadata — and the review ledger, which is keyed on id,
    then maps one id to two different clips. That is not theoretical: a
    rerun overwrote a clip that had already been published, leaving the
    ledger pointing the new files at the old video's URL and causing the
    duplicate-publish guard to swallow a genuine approval.

    Existing ids are left alone so readable names survive; only a genuine
    collision gets a suffix.
    """
    output_dir = Path(output_dir)
    candidate, n = clip_id, 2
    while (output_dir / f"{candidate}_9x16.mp4").exists() or \
          (output_dir / f"{candidate}_metadata.json").exists():
        candidate = f"{clip_id}_{n}"
        n += 1
    return candidate


class VerdandiOrchestrator:
    """
    Orchestrates Gemini-driven clip selection and delegates rendering to
    Skuld / telemetry logging to Urðr.

    Each call to `orchestrate_generation` builds its own bound tool
    closures rather than relying on module-level globals, and explicitly
    injects Urðr's ClickHouse-derived retention intelligence into the
    prompt — the "grounds decisions in Urðr's telemetry" step the
    architecture diagram describes, actually wired into the request.
    """

    # Every call this class makes is to the same model, so one client built
    # for it is enough — but it must be built through the factory, or the
    # reasoning call goes to AI Studio while the video sits in the Cloud
    # Storage bucket that only Vertex can read.
    MODEL = "gemini-3.6-flash"

    def __init__(self, project_id: str = None):
        from agent import genai_client as gc

        api_key = os.getenv("GEMINI_API_KEY")
        self.client, self.model = gc.client_for(self.MODEL, api_key=api_key)
        self.project_id = project_id
        self.skuld = SkuldRenderer(output_dir="output_clips")
        self.urdr = UrdrAnalytics()
        self.bragi = BragiComposer()
        self.heimdall = HeimdallVisualizer()
        self.mimir = MimirNarrator()
        # Keyed by (transcript_text, target_language) — the same window's
        # transcript is typically reused across every clip in a single
        # generation run, so translating it once per language instead of
        # once per clip avoids redundant Gemini calls.
        self._translation_cache: Dict[Tuple[str, str], str] = {}

    def translate_transcript(self, transcript_text: str, target_language: str) -> str:
        """
        Translates each transcript line into target_language while
        preserving the exact same [MM:SS] timestamp prefix and line
        structure — so the result is a drop-in replacement for the
        original-language transcript when fed into Skuld's subtitle
        renderer (generate_rebased_ass_subtitle_file), which keys caption
        timing purely off those timestamps. Only the burned-in captions
        change; Verðandi still reasons over (and Mímir's enhance-narration
        fallback still reads back) the original-language text.
        A translation failure falls back to the original text rather than
        blocking the render.
        """
        cache_key = (transcript_text, target_language)
        if cache_key in self._translation_cache:
            return self._translation_cache[cache_key]

        prompt = (
            f"Translate the spoken text on each line below into {target_language}. "
            "Keep the exact same line order and the exact same [MM:SS] (or "
            "[MM:SS]-[MM:SS]) timestamp prefix on every line — only replace the "
            "words after the timestamp with their translation. Output one line "
            "per input line, nothing else: no headers, no notes, no commentary.\n\n"
            f"{transcript_text}"
        )
        try:
            response = self.client.models.generate_content(model=self.model, contents=prompt)
            translated = (response.text or "").strip()
            if not translated:
                logger.warning("Transcript translation returned empty text; falling back to original.")
                translated = transcript_text
        except Exception as e:
            logger.warning(f"Transcript translation to '{target_language}' failed ({e}); falling back to original.")
            translated = transcript_text

        self._translation_cache[cache_key] = translated
        return translated

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
        vision_mode: bool = False,
        clip_id_prefix: str = "",
        caption_language: Optional[str] = None,
        caption_font: Optional[str] = None,
        music_mood: Optional[str] = None,
        avoid_motion: Optional[List[str]] = None,
        avoid_crop: Optional[List[str]] = None,
        opener_sec: float = 0.0,
        progress_callback: Optional[Callable[[str, str], None]] = None,
    ) -> List[Callable]:
        """Builds request-scoped tool functions closing over this call's state."""

        # Counts which clip (1-indexed) is currently rendering, purely for
        # progress_callback's "clip N/target_count" labeling below — target
        # count itself isn't known here, so the UI side fills that in.
        clip_counter = [0]

        def _emit(stage: str, message: str) -> None:
            # A UI-side progress callback is a nice-to-have, never a
            # dependency: a bug or exception in it (e.g. a stale Streamlit
            # placeholder) must never take down an otherwise-successful
            # generation run.
            if progress_callback is None:
                return
            try:
                progress_callback(stage, message)
            except Exception as e:
                logger.debug(f"progress_callback raised (ignored): {e}")

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
                # Pin the start inside the window AND far enough from its
                # end that a minimum-length clip still fits. Clamping
                # start and end independently is not enough: for a range
                # entirely outside the window (say 05:00-05:02 against a
                # 02:00-03:00 window) start clamps UP to the window start
                # while end clamps DOWN to the window end, and the two
                # cross -- yielding end BEFORE start and handing FFmpeg an
                # invalid "-ss 05:00 -to 03:00". Found by the property
                # test in tests/test_verdandi_orchestrator.py.
                latest_start = max(window_start, window_end - min_duration_sec)
                start_sec = min(max(start_sec, window_start), latest_start)
                end_sec = min(max(end_sec, start_sec), window_end)

            duration = end_sec - start_sec
            if duration > max_duration_sec:
                end_sec = start_sec + max_duration_sec
            elif duration < min_duration_sec:
                end_sec = start_sec + min_duration_sec

            end_sec = min(end_sec, safe_video_end_sec)  # never exceed the actual source video's length
            if window is not None:
                end_sec = min(end_sec, window[1])

            # Final invariant: a clip must always have positive length,
            # whatever combination of caps was applied above.
            if end_sec <= start_sec:
                start_sec = max(0.0, min(start_sec, end_sec - min_duration_sec))
                end_sec = max(end_sec, start_sec + min(min_duration_sec, safe_video_end_sec - start_sec))

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
            transcript_text_override: str = "",
            segment_has_full_width_graphics: bool = False,
        ) -> str:
            """
            Renders a 9:16 vertical short with FFmpeg, burning in kinetic
            subtitles derived from the full source transcript. Always pass
            a unique clip_id per clip. Leave transcript_text_override empty
            to automatically use the full source transcript for subtitle
            generation. hook_type must be the same value you will later
            pass to tool_log_urdr_telemetry for this clip_id — it's used
            here up front to ground Bragi's Lyria-composed background
            score AND the clip's crop framing/camera motion/color grade in
            Urðr's historical benchmarks for that hook type. There is still
            no crop_mode parameter to set: the visual treatment is looked up
            from real ClickHouse data for this hook_type, not chosen ad hoc.

            segment_has_full_width_graphics is an observation about THIS
            footage, not a styling choice. Set it true when the segment
            carries titles, captions, labelled diagrams or any text that
            runs close to the left and right edges of the source frame.
            Filling a 9:16 screen means cutting the sides off a 16:9 source,
            which is right for centred action and destroys full-width text
            -- a NASA explainer rendered that way turned "LUNAR LANDINGS"
            into "NAR LANDIN". No benchmark can know this, because it is a
            property of the video rather than of the hook type, and you are
            the only part of this pipeline that can see it. When it is true
            the ranking simply chooses among the crop modes that keep the
            full width.
            """
            # Namespaced so batch mode (independent orchestrate_generation
            # calls, one per source video) can't collide: Gemini often
            # picks generic clip_id values like "clip_1" on its own, and
            # without a prefix, two different videos' "clip_1" would
            # silently overwrite each other's rendered file. Gemini itself
            # never sees the prefix — it keeps calling both tools with
            # whatever plain clip_id it chose.
            clip_id = unique_clip_id(f"{clip_id_prefix}{clip_id}", self.skuld.output_dir)
            clip_counter[0] += 1
            logger.info(f"Executing Skuld render for clip_id: {clip_id} ({start_time} to {end_time})")
            # Align to sentence boundaries first, then clamp. Reviewers
            # rejected clips for starting mid-thought and stopping
            # mid-sentence; the clamp knows about duration and the source
            # window and nothing about language. Clamping last keeps those
            # limits authoritative, so snapping can never push a clip
            # outside the requested range or duration.
            _snap_start, _snap_end = snap_to_sentences(
                parse_time_to_seconds(start_time),
                parse_time_to_seconds(end_time),
                # The full transcript, not the window-filtered one: that is
                # resolved further down, and sentence boundaries just either
                # side of a window edge are exactly the ones worth snapping to.
                transcript_text,
            )
            start_time, end_time = _clamp_duration(
                format_seconds_to_mmss(_snap_start), format_seconds_to_mmss(_snap_end))
            resolved_transcript = (
                transcript_text_override
                if transcript_text_override and len(transcript_text_override.strip()) > 20
                else transcript_text
            )

            # Burned-in captions can be translated into a different
            # language than the one Verðandi reasons in above and Mímir's
            # enhance-narration fallback reads back below — only the text
            # that actually gets rendered onto screen changes. Timestamps
            # are preserved line-for-line, so chunk timing is unaffected.
            caption_transcript = resolved_transcript
            if caption_language and resolved_transcript:
                caption_transcript = self.translate_transcript(resolved_transcript, caption_language)

            # Ground Bragi's Lyria prompt in real ClickHouse virality data
            # for this hook_type, then compose (or reuse a cached) track.
            # A composition failure never blocks the clip — render simply
            # proceeds without music.
            music_path = None
            music_benchmark = self.urdr.get_top_music_benchmark(
                hook_type=hook_type, topic_category=topic_focus, mood=music_mood)
            if music_benchmark:
                _emit("bragi", f"🎵 Bragi (music) is composing a {music_benchmark.get('mood', 'custom')} score (clip {clip_counter[0]})...")
                music_path = self.bragi.compose_track(hook_type, music_benchmark)

            # Ground the clip's crop framing, camera motion, and color grade
            # in real ClickHouse virality data for this hook_type — same
            # principle as Bragi's music choice above: rather than a crop
            # style chosen ad hoc per render, the visual treatment is
            # looked up from historical hook_type performance.
            # The channel's standing exclusions, plus anything this
            # particular segment rules out. The benchmark still ranks; it
            # just ranks within what the footage allows.
            crop_exclusions = list(avoid_crop or [])
            if segment_has_full_width_graphics:
                crop_exclusions += [m for m in SIDE_CROPPING_MODES
                                    if m not in crop_exclusions]
                logger.info(
                    f"{clip_id}: segment reported as carrying full-width "
                    f"graphics, so side-cropping modes are excluded.")
            visual_benchmark = self.urdr.get_top_visual_benchmark(
                hook_type=hook_type, topic_category=topic_focus,
                avoid_motion=avoid_motion, avoid_crop=crop_exclusions)
            crop_mode = visual_benchmark.get("crop_mode", "center_crop") if visual_benchmark else "center_crop"
            motion_effect = visual_benchmark.get("motion_effect", "none") if visual_benchmark else "none"
            color_grade = visual_benchmark.get("color_grade", "neutral") if visual_benchmark else "neutral"

            # Heimdall composes a custom cover thumbnail grounded in the
            # same music_benchmark row — the mood/genre/energy that suits
            # this hook_type acoustically is the same signal that should
            # drive its visual mood. A generation failure never blocks the
            # clip — the render simply falls back to no custom thumbnail.
            thumbnail_path = None
            if music_benchmark:
                _emit("heimdall", f"👁️ Heimdall (cover art) is generating the cover thumbnail (clip {clip_counter[0]})...")
                thumbnail_path = self.heimdall.compose_thumbnail(
                    clip_id=clip_id, hook_title=hook_banner_text, music_benchmark=music_benchmark,
                    output_dir=self.skuld.output_dir,
                )

            # Mímir narrates in two situations, both grounded in the same
            # music_benchmark's energy_level for voice selection:
            #  1. Fill silence — vision mode has no dialogue at all, so
            #     the hook line itself becomes the script.
            #  2. Enhance — a transcript exists, but this specific clip's
            #     sliced audio measured too quiet to reliably follow, so
            #     the actual transcript text for this window is narrated
            #     back clearly rather than substituted with a hook line.
            # A generation failure never blocks the clip — render simply
            # proceeds without narration, same as Bragi/Heimdall.
            narration_path = None
            energy_level = float(music_benchmark.get("energy_level", 0.5)) if music_benchmark else 0.5
            if vision_mode:
                _emit("mimir", f"🗣️ Mímir (narration) is narrating the hook line (clip {clip_counter[0]})...")
                narration_path = self.mimir.narrate(
                    clip_id=clip_id, script_text=hook_banner_text, energy_level=energy_level,
                    output_dir=self.skuld.output_dir,
                )
            elif resolved_transcript:
                mean_volume = measure_audio_mean_volume(input_video_path, start_time, end_time)
                if mean_volume is not None and mean_volume < NARRATION_FALLBACK_VOLUME_THRESHOLD_DB:
                    start_sec = parse_time_to_seconds(start_time)
                    end_sec = parse_time_to_seconds(end_time)
                    window_slice = filter_transcript_by_window(resolved_transcript, (start_sec, end_sec))
                    window_text = _clean_transcript_window_text(window_slice)
                    if window_text:
                        logger.info(
                            f"Clip '{clip_id}' audio measured {mean_volume:.1f}dB "
                            f"(below {NARRATION_FALLBACK_VOLUME_THRESHOLD_DB}dB threshold) — "
                            f"narrating via Mímir fallback."
                        )
                        _emit("mimir", f"🗣️ Mímir (narration) is narrating over hard-to-hear audio (clip {clip_counter[0]})...")
                        narration_path = self.mimir.narrate(
                            clip_id=clip_id, script_text=window_text, energy_level=energy_level,
                            output_dir=self.skuld.output_dir,
                        )

            _emit("skuld", f"🎬 Skuld (rendering) is cutting the vertical short via FFmpeg (clip {clip_counter[0]})...")
            result = self.skuld.render_vertical_short(
                input_video_path=input_video_path,
                start_time=start_time,
                end_time=end_time,
                clip_id=clip_id,
                crop_mode=crop_mode,
                motion_effect=motion_effect,
                caption_font=caption_font,
                color_grade=color_grade,
                hook_banner_text=hook_banner_text,
                transcript_text=caption_transcript,
                warmth=warmth,
                crazy=crazy,
                music_path=music_path,
                narration_path=narration_path,
            )
            # Record ground-truth render output. This is what the UI will
            # ultimately trust, independent of whatever the model's final
            # text summary says — so a malformed closing JSON response can
            # never orphan a clip that actually rendered successfully.
            # A generated opening shot, if one was asked for. Off unless a
            # caller sets opener_sec, because every opener is a paid Veo
            # call on a clip that otherwise costs only ffmpeg.
            #
            # Degrades here even though weave_opener raises: the raise is
            # what stops a half-joined file being written, and losing the
            # opener should never cost the clip that already rendered.
            rendered_path = result["output_video_path"]
            has_opener = False
            if opener_sec and opener_sec > 0:
                _emit("skuld", f"🧵 Weaving a generated opener (clip {clip_counter[0]})...")
                try:
                    from agent import weaver
                    woven = weaver.add_generated_opener(
                        clip_path=rendered_path,
                        hook_title=hook_banner_text,
                        clip_id=clip_id,
                        topic=topic_focus or "",
                        opener_sec=opener_sec)
                    rendered_path = str(woven.path)
                    has_opener = True
                    logger.info(f"{clip_id}: {woven.note}")
                except Exception as e:
                    logger.warning(
                        f"{clip_id}: no generated opener, keeping the clip as "
                        f"rendered: {e}")

            # What the viewer actually hears first, and whether it will
            # lose them. Recorded on the clip so a reviewer sees it in the
            # staging email rather than discovering it by watching.
            opening = clip_opening_line(
                resolved_transcript, parse_time_to_seconds(start_time))
            opening_problem = weak_opening(opening)
            if opening_problem:
                logger.warning(
                    f"{clip_id}: weak opening line — {opening_problem}: {opening[:70]!r}")

            rendered_clips.append(
                {
                    "clip_id": clip_id,
                    "start_time": start_time,
                    "end_time": end_time,
                    "opening_line": opening,
                    "opening_problem": opening_problem,
                    "output_video_path": rendered_path,
                    "has_generated_opener": has_opener,
                    "has_subtitles": result["has_subtitles"],
                    "has_bragi_score": result.get("has_bragi_score", False),
                    "has_narration": result.get("has_narration", False),
                    "caption_language": caption_language,
                    "thumbnail_path": thumbnail_path,
                    "music_genre": music_benchmark.get("genre") if music_benchmark else None,
                    "music_mood": music_benchmark.get("mood") if music_benchmark else None,
                    "crop_mode": result.get("crop_mode", "unknown"),
                    "motion_effect": result.get("motion_effect", "none"),
                    "color_grade": result.get("color_grade", "neutral"),
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
            # Same namespacing as tool_execute_skuld_render, so the
            # rendered_clips lookup below actually matches — Gemini calls
            # this with the same plain clip_id it originally chose.
            #
            # Resolved from the render records rather than recomputed:
            # unique_clip_id is stateful in the filesystem, and running it
            # again here would see the file the render just wrote and hand
            # back the next free suffix instead of the one in use.
            namespaced = f"{clip_id_prefix}{clip_id}"
            clip_id = next(
                (c["clip_id"] for c in rendered_clips
                 if c["clip_id"] == namespaced or c["clip_id"].startswith(f"{namespaced}_")),
                namespaced)
            logger.info(f"Logging Urðr telemetry for clip_id: {clip_id}, hook_type: {hook_type}")
            _emit("urdr_log", f"📊 Urðr (analytics) is logging telemetry for clip {clip_counter[0]}...")

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
                motion_effect=match.get("motion_effect", "unknown") if match else "unknown",
                color_grade=match.get("color_grade", "unknown") if match else "unknown",
            )
            return json.dumps({"logged": success, "clip_id": clip_id, "hook_rank": hook_rank, "is_top_tier": is_top_tier})

        return [tool_execute_skuld_render, tool_log_urdr_telemetry]

    def _build_prompt(
        self, transcript_text: str, video_path: str, target_count: int, retention_summary: Dict[str, Any],
        min_duration_sec: float, max_duration_sec: float, video_duration_sec: float,
        vision_mode: bool = False, target_duration_sec: Optional[float] = None,
        window: Optional[Tuple[float, float]] = None, content_hint: Optional[str] = None,
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
        # Files API, see _upload_video) instead of transcript text —
        # timestamps come from what it actually sees/hears in the video
        # rather than from transcript-anchored cues.
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
            content_instruction = (
                f"Analyze this transcript:\n{transcript_text}\n\n"
                f"The full source video is also attached directly above this prompt — actually listen to "
                f"the real vocal delivery in it, not just the transcript's word content. Judge whether the "
                f"speaker's real tone, energy, and pacing in each candidate window genuinely supports the "
                f"hook_type you're leaning toward: 'shock_stat' and 'contrarian_claim' need a punchy, "
                f"urgent, or emphatic delivery to actually land as that hook — if the real speaker sounds "
                f"flat, monotone, or hesitant in that specific window, prefer a hook_type the real delivery "
                f"actually supports (e.g. 'metaphor_analogy' or 'story_in_medias_res' read calmly) over one "
                f"that only fits on paper. The words and the delivery must both earn the hook_type, not just "
                f"the words."
            )

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

        content_hint_instruction = ""
        if content_hint:
            content_hint_instruction = (
                f"Creative direction from the user: \"{content_hint}\". Prioritize a moment that genuinely "
                f"matches this direction over one that might otherwise score higher on the retention "
                f"benchmarks alone — the user's explicit creative intent outranks a marginal virality-score "
                f"gain. If nothing in this video plausibly matches the direction, pick the closest honest "
                f"fit rather than fabricating one that isn't there.\n\n"
            )

        return (
            f"Historical Urðr ClickHouse retention intelligence — ground your hook_type "
            f"selection in this real data, don't ignore it:\n{grounding_json}\n\n"
            f"{topic_instruction}"
            f"{content_hint_instruction}"
            f"{content_instruction}\n\n"
            f"Source Video Path: {video_path}\n"
            f"CRITICAL: The video is {video_duration_mmss} (MM:SS) long. Generate exactly {target_count} clips. "
            f"{window_instruction}"
            f"You MUST choose a start_time and end_time strictly between 00:00 and {safe_video_end_mmss}"
            + (" that matches the transcript timestamps. " if not vision_mode else " based on what you observe directly in the attached video. ")
            + "The hook_taxonomies list is ordered by measured performance for this channel's size band: "
              "entries with global_measured=true carry median views from real English-titled YouTube "
              "videos, best first, and global_lift_vs_plain_pct is their lift over an unstyled title. "
              "Prefer a higher-ranked hook unless the transcript genuinely does not support it — a "
              "forced hook reads worse than an honest one. Entries with global_measured=false have no "
              "global measurement and are ordered behind the measured ones.\n"
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

    def _upload_video(self, video_path: str, timeout_sec: float = 120.0):
        """
        Make the source video available to the model, whichever surface
        this is running against.

        The two do it completely differently. AI Studio has a Files API
        that takes an upload and processes it. Vertex has no Files API at
        all — the SDK answers "This method is only supported in the Gemini
        Developer client" — and reads instead from Cloud Storage, so the
        file has to be put in a bucket and referenced by URI.

        Returns a content part either way, which is all the caller needs:
        a types.File and a types.Part are both valid entries in the list
        passed to send_message.
        """
        from agent import genai_client as gc

        if gc.use_vertex():
            return self._upload_video_to_gcs(video_path)
        return self._upload_video_to_files_api(video_path, timeout_sec)

    def _upload_video_to_gcs(self, video_path: str):
        """
        Put the video in Cloud Storage and hand back a gs:// reference.

        The bucket has a short lifecycle rule because nothing here deletes
        what it uploads: the Files API expired its uploads by itself after
        48 hours, and moving to Cloud Storage silently drops that, so
        without a rule every run of the pipeline would leave a permanent
        copy of its source video behind.
        """
        from google.cloud import storage

        bucket_name = os.getenv("NORNPULSE_MEDIA_BUCKET", DEFAULT_MEDIA_BUCKET)
        source = Path(video_path)
        # Namespaced by content so a repeated run of the same source reuses
        # the object rather than uploading fifty megabytes again.
        key = f"sources/{_file_digest(source)}{source.suffix or '.mp4'}"
        uri = f"gs://{bucket_name}/{key}"

        client = storage.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT")
                                or os.getenv("NORNPULSE_VERTEX_PROJECT"))
        blob = client.bucket(bucket_name).blob(key)

        _t0 = time.perf_counter()
        if blob.exists():
            logger.info(f"Reusing {uri} — already uploaded.")
        else:
            logger.info(f"Uploading '{video_path}' to {uri}...")
            blob.upload_from_filename(str(source))
            logger.info(
                f"⏱️ Upload of {source.stat().st_size / 1e6:.1f} MB took "
                f"{time.perf_counter() - _t0:.1f}s")

        return types.Part.from_uri(file_uri=uri, mime_type="video/mp4")

    def _upload_video_to_files_api(self, video_path: str,
                                   timeout_sec: float = 120.0) -> types.File:
        """
        Uploads the source video to Gemini's Files API and blocks until
        it's ACTIVE (processed and ready to reason over) or the timeout
        elapses. Called on every generation, not just vision mode (no
        transcript) — Verðandi always gets the actual video/audio now, so
        it can judge real vocal delivery/energy when picking hook_type,
        not just word content from the transcript. This means every
        generation pays the ~5-20s upload+processing latency vision-mode
        clips already paid, and video-token billing instead of text-only.
        """
        logger.info(f"Uploading '{video_path}' to Gemini Files API...")
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
            f"⏱️ Video upload + processing took {time.perf_counter() - _t0:.1f}s "
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
        auto_window_mode: str = "random",
        clip_id_prefix: str = "",
        content_hint: Optional[str] = None,
        caption_language: Optional[str] = None,
        channel_subscribers: int = 0,
        caption_font: Optional[str] = None,
        source_ref: Optional[str] = None,
        channel_profile: Optional[Any] = None,
        opener_sec: float = 0.0,
        rewatch_evidence: str = "",
        rewatch_peak_sec: Optional[float] = None,
        progress_callback: Optional[Callable[[str, str], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        source_ref records what this clip was cut from -- a URL or a file
        name -- and is stamped onto every returned clip so a published
        video can always answer whose material it derives from.

        progress_callback, if given, is called as (stage_key, message) at
        each stage transition (urdr, upload, verdandi, bragi, heimdall,
        mimir, skuld, urdr_log) — e.g. to drive a live pipeline-stage UI.
        Entirely optional and best-effort: exceptions inside it are
        swallowed (see _make_tools._emit) so a UI bug can never break an
        otherwise-successful generation.
        """
        def _emit(stage: str, message: str) -> None:
            if progress_callback is None:
                return
            try:
                progress_callback(stage, message)
            except Exception as e:
                logger.debug(f"progress_callback raised (ignored): {e}")

        rendered_clips: List[Dict[str, Any]] = []

        # Detect the actual source video length instead of assuming the
        # ~53s demo asset — this is what makes the pipeline work with any
        # video, not just the one it was originally built against.
        try:
            video_duration_sec = get_video_duration_seconds(video_path)
        except Exception as e:
            logger.warning(f"Could not detect video duration via ffprobe ({e}); falling back to 53.0s assumption.")
            video_duration_sec = 53.0

        # Long video, no manual window set: auto-pick one (random offset,
        # or from the start) rather than reasoning over the entire
        # runtime in a single call. Only kicks in when the caller hasn't
        # already narrowed things down themselves.
        if transcript_window is None and video_duration_sec > AUTO_WINDOW_MAX_SEC:
            window_start = auto_window_start(
                video_duration_sec, auto_window_mode, rewatch_peak_sec)
            window_end = window_start + AUTO_WINDOW_MAX_SEC
            transcript_window = (window_start, window_end)
            logger.info(
                f"Video is {video_duration_sec:.0f}s (> {AUTO_WINDOW_MAX_SEC:.0f}s) with no manual window set — "
                f"auto-selected a {AUTO_WINDOW_MAX_SEC:.0f}s window via '{auto_window_mode}' mode: "
                f"{format_seconds_to_mmss(window_start)}-{format_seconds_to_mmss(window_end)}."
            )

        # A manually-narrowed (or now, possibly auto-selected) transcript
        # window is a hard constraint,
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
        _emit("urdr", "🔮 Urðr (analytics) is pulling ClickHouse retention benchmarks...")
        _t0 = time.perf_counter()
        # Channel size decides which band the hook ranking is read from:
        # curiosity_gap leads for a new channel, story_in_medias_res for a
        # large one, so an unbanded ranking would be wrong for both.
        retention_summary = self.urdr.get_retention_intelligence_summary(
            topic_category=topic_focus, channel_subscribers=channel_subscribers)
        logger.info(f"⏱️ Retention summary fetch took {time.perf_counter() - _t0:.1f}s")

        # vision_mode still means "no usable transcript" (silent/
        # instrumental source, or extraction failed) — Verðandi reasons
        # entirely from the video/audio rather than transcript-anchored
        # cues in that case. But the video itself is now ALWAYS uploaded
        # and attached, transcript or not: even transcript-driven clips
        # need Gemini to actually see/hear the real vocal delivery, not
        # just judge hook_type fit from word content alone.
        vision_mode = not transcript_text or not transcript_text.strip()
        _emit("upload", "📤 Uploading video to Gemini...")
        video_file = self._upload_video(video_path)

        tools = self._make_tools(
            transcript_text, rendered_clips, warmth, crazy, retention_summary,
            min_duration_sec, max_duration_sec, video_duration_sec,
            topic_focus=topic_focus, window=transcript_window, vision_mode=vision_mode,
            clip_id_prefix=clip_id_prefix, caption_language=caption_language,
            caption_font=caption_font,
            # The channel's own editorial constraints. Without these the
            # seeded benchmarks decide alone, and they are ranked on a
            # generic taxonomy: a space channel was given synthwave because
            # synthwave scores highest overall, and shake because shake does.
            music_mood=getattr(getattr(channel_profile, "music_mood", None), "strip", lambda: None)()
            if getattr(channel_profile, "music_mood", None) else None,
            avoid_motion=list(getattr(channel_profile, "avoid_motion", []) or []),
            avoid_crop=list(getattr(channel_profile, "avoid_crop", []) or []),
            opener_sec=opener_sec,
            progress_callback=progress_callback,
        )
        prompt = self._build_prompt(
            transcript_text, video_path, target_count, retention_summary,
            min_duration_sec, max_duration_sec, video_duration_sec,
            vision_mode=vision_mode, target_duration_sec=target_duration_sec,
            window=transcript_window, content_hint=content_hint,
        )
        safe_video_end_mmss = format_seconds_to_mmss(max(min_duration_sec, video_duration_sec - 1.0))

        try:
            chat = self.client.chats.create(
                model=self.model,
                config=types.GenerateContentConfig(
                    tools=tools,
                    system_instruction=(
                        f"You are Verðandi. Select valid start/end times strictly within the "
                        f"00:00-{safe_video_end_mmss} range for this video. Ground every hook_type "
                        f"selection in the Urðr retention intelligence provided in the prompt — prefer "
                        f"higher-virality hook types "
                        "when the source content genuinely fits that framing, rather than "
                        "defaulting to the same hook type regardless of content. You have the actual "
                        "video attached, not just transcript text where one exists — weigh the real "
                        "vocal delivery and energy you observe, not just word content, when a hook_type "
                        "implies a particular tone. " + (rewatch_evidence or "") +
                        "AVOID THE SOURCE'S OWN TITLE "
                        "CARDS: a long video punctuates itself with full-screen "
                        "chapter headings and lower-third captions. Those are the "
                        "source's furniture, not its content -- they carry no "
                        "information for someone arriving cold, they are the frames "
                        "most likely to be ruined by cropping to vertical, and they "
                        "waste the one second that decides whether anyone stays. "
                        "Start after the card, on the footage it introduces. Prefer "
                        "segments where something is visibly happening over segments "
                        "of static graphics or a motionless talking head. "
                        "CUT ON THE HOOK: a Short has about "
                        "one second to justify itself, so start_time must land on the most "
                        "arresting line available in the segment, not on the chronological "
                        "start of the thought that contains it. A first line beginning with "
                        "\"And\", \"So\", \"But\", \"Because\", \"Now\" or a pronoun with "
                        "nothing to refer to yet tells the viewer they walked in halfway "
                        "through, and they leave. Prefer a line carrying a number, a "
                        "superlative, a question or a concrete noun; move start_time "
                        "forward to reach one, and let the setup arrive afterwards or not "
                        "at all. Since you can see the frames, you are "
                        "also the only part of this pipeline that knows whether a segment "
                        "carries burned-in titles, captions or labelled diagrams running "
                        "close to the left and right edges — look, and set "
                        "segment_has_full_width_graphics accordingly. Filling a vertical "
                        "frame means cutting the sides off a widescreen source, which is "
                        "right for centred action and destroys full-width text. Always call "
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
            _emit("verdandi", "🧠 Verðandi (reasoning) is choosing hook types & moments...")
            _t1 = time.perf_counter()
            response = chat.send_message([video_file, prompt])
            logger.info(f"⏱️ Gemini reasoning + all tool calls took {time.perf_counter() - _t1:.1f}s total")
            text_output = response.text if response and response.text else ""
            parsed_metadata = self._parse_model_json(text_output)

        except Exception as e:
            logger.error(f"Verðandi orchestration execution failed: {e}")
            raise e

        _emit("done", "✨ Generation complete.")
        return self._reconcile_metadata(
            parsed_metadata, rendered_clips,
            clip_id_prefix=clip_id_prefix, source_ref=source_ref)

    def orchestrate_batch(
        self,
        video_urls: List[str],
        target_count_per_video: int = 1,
        warmth: float = 0.5,
        crazy: float = 0.3,
        topic_focus: Optional[str] = None,
        min_duration_sec: float = 8.0,
        max_duration_sec: float = 15.0,
        cut_energy: float = 0.5,
        auto_window_mode: str = "random",
        content_hint: Optional[str] = None,
        caption_language: Optional[str] = None,
        channel_subscribers: int = 0,
        channel_profile: Optional[Any] = None,
        opener_sec: float = 0.0,
        rewatch_evidence: str = "",
        rewatch_peak_sec: Optional[float] = None,
        progress_callback: Optional[Callable[[str, str], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Runs the full single-video pipeline once per URL in video_urls
        (capped at BATCH_MAX_VIDEOS — extra URLs are ignored, not queued),
        downloading and transcribing each one exactly like the single-
        video UI flow does, then returns every resulting clip's metadata
        sorted by predicted virality_score descending — a ranked slate
        across multiple source videos instead of reviewing one video's
        results at a time. No new capability per video: this is the same
        orchestrate_generation() call already used everywhere else, just
        looped, with results merged and ranked at the end.

        A failure on any single video (download, transcription, or
        generation) is logged and that video is skipped rather than
        aborting the whole batch — one bad URL in a playlist shouldn't
        cost the videos that were fine.
        """
        urls = video_urls[:BATCH_MAX_VIDEOS]
        if len(video_urls) > BATCH_MAX_VIDEOS:
            logger.info(f"Batch capped at {BATCH_MAX_VIDEOS} videos ({len(video_urls)} were provided).")

        # Batch runs the whole pipeline once per video, so without this the
        # UI sits silent for many minutes. Each per-video stage message is
        # prefixed with "Video i/N" and the inner orchestrate_generation's
        # own stage messages are re-emitted with the same prefix, so the
        # stepper shows both which video and which Norn is working.
        def _emit(stage: str, message: str) -> None:
            if progress_callback is None:
                return
            try:
                progress_callback(stage, message)
            except Exception as e:
                logger.debug(f"progress_callback raised (ignored): {e}")

        all_clips: List[Dict[str, Any]] = []
        for i, url in enumerate(urls):
            try:
                # Same fix as the single-video UI flow: check length before
                # downloading anything, and download only a bounded window
                # for a long video rather than the whole file just to
                # window it down afterward.
                download_time_range = None
                try:
                    probed_duration = get_youtube_duration(url)
                    if probed_duration > AUTO_WINDOW_MAX_SEC:
                        window_start = (
                            random.uniform(0.0, probed_duration - AUTO_WINDOW_MAX_SEC)
                            if auto_window_mode != "start" else 0.0
                        )
                        download_time_range = (window_start, window_start + AUTO_WINDOW_MAX_SEC)
                except Exception as e:
                    logger.warning(f"Could not check length of {url} ahead of download ({e}); downloading normally.")

                label = f"Video {i + 1}/{len(urls)}"
                logger.info(f"🗂️ Batch {i + 1}/{len(urls)}: downloading {url}")
                _emit("download", f"🗂️ {label}: downloading source...")
                video_path = download_youtube_video(
                    url, output_filename=f"batch_{i}_input.mp4", time_range=download_time_range,
                )
                _emit("transcribe", f"🗂️ {label}: extracting transcript...")
                transcript_text = get_or_create_transcript(video_path)

                # What viewers of the original actually scrubbed back to.
                # Measured, about this exact video, and free -- it comes
                # from the same metadata probe the download already makes.
                rewatch_evidence = ""
                rewatch_peak_sec = None
                try:
                    from agent import heatmap as hm
                    moments = hm.fetch(url)
                    rewatch_evidence = hm.describe(
                        moments, duration_sec=probed_duration or None)
                    found = hm.peaks(moments)
                    if rewatch_evidence:
                        logger.info(
                            f"Most-replayed graph: {len(moments)} buckets, "
                            f"{len(found)} peaks worth naming.")
                    if found:
                        rewatch_peak_sec = found[0].mid_sec
                except Exception as e:
                    logger.info(f"No re-watch evidence for {url}: {str(e)[:100]}")

                # Re-emit the inner pipeline's stage events with the batch
                # prefix, so the stepper keeps lighting up the right Norn
                # while also saying which video it's on.
                def _relay(stage: str, message: str, _label: str = label) -> None:
                    _emit(stage, f"🗂️ {_label} · {message}")

                clips = self.orchestrate_generation(
                    transcript_text=transcript_text,
                    video_path=video_path,
                    target_count=target_count_per_video,
                    warmth=warmth,
                    crazy=crazy,
                    topic_focus=topic_focus,
                    min_duration_sec=min_duration_sec,
                    max_duration_sec=max_duration_sec,
                    cut_energy=cut_energy,
                    auto_window_mode=auto_window_mode,
                    clip_id_prefix=f"batch{i}_",
                    content_hint=content_hint,
                    caption_language=caption_language,
                    channel_subscribers=channel_subscribers,
                    source_ref=url,
                    channel_profile=channel_profile,
                    opener_sec=opener_sec,
                    rewatch_evidence=rewatch_evidence,
                    rewatch_peak_sec=rewatch_peak_sec,
                    progress_callback=_relay,
                )
                all_clips.extend(clips)
            except Exception as e:
                # Unwrap tenacity: a RetryError's str() is
                # "RetryError[<Future at 0x... state=finished raised
                # TypeError>]", which names neither the message nor a line.
                # A skipped video is meant to cost that video, not the
                # ability to find out why it was skipped.
                cause = e
                last = getattr(e, "last_attempt", None)
                if last is not None and last.failed:
                    cause = last.exception()
                logger.error(
                    f"Batch item {i + 1}/{len(urls)} ({url}) failed, skipping: "
                    f"{type(cause).__name__}: {cause}",
                    exc_info=cause)
                continue

        all_clips.sort(key=lambda c: c.get("virality_score", 0.0), reverse=True)
        logger.info(f"🗂️ Batch complete: {len(all_clips)} clips from {len(urls)} videos, ranked by virality_score.")
        _emit("done", f"✨ Batch complete: {len(all_clips)} clip(s) from {len(urls)} video(s).")
        return all_clips

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
        self, parsed_metadata: List[Dict[str, Any]], rendered_clips: List[Dict[str, Any]],
        clip_id_prefix: str = "", source_ref: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Merges the model's descriptive metadata (hook_title, social_caption,
        virality_score) with the authoritative render + telemetry records
        (clip_id, output_video_path, hook_type, grounding alignment), keyed
        by clip_id. This guarantees the UI never points at a file that
        doesn't exist, and always shows real alignment data rather than
        whatever the model's closing text happened to say.

        parsed_metadata's clip_id values are Gemini's own plain (unprefixed)
        choice, while rendered_clips' are namespaced with clip_id_prefix
        (see tool_execute_skuld_render) — the same prefix is applied here
        so the two actually match up instead of every clip falling back to
        generic defaults.
        """
        if not rendered_clips:
            logger.error("No clips were actually rendered by Skuld this run.")
            return []

        by_id = {
            f"{clip_id_prefix}{m.get('clip_id')}": m
            for m in parsed_metadata if isinstance(m, dict)
        }
        final: List[Dict[str, Any]] = []

        for clip in rendered_clips:
            # A collision suffix (see unique_clip_id) means the rendered id
            # can be the model's id plus "_2". Matching only on equality
            # silently dropped the model's copy and every such clip came
            # back titled "Autonomous Core Insight" with a default score.
            meta = by_id.get(clip["clip_id"])
            if meta is None:
                meta = next(
                    (m for key, m in by_id.items()
                     if clip["clip_id"].startswith(f"{key}_")
                     and clip["clip_id"][len(key) + 1:].isdigit()),
                    {})
            # Start from the render record wholesale rather than copying
            # fields across one by one. The previous field-by-field build
            # silently dropped anything not explicitly listed, so
            # crop_mode, motion_effect, color_grade and caption_language
            # all arrived as None in the UI and in the saved
            # {clip_id}_metadata.json -- the caption_language badge
            # ("translated to X") could therefore never render, even for
            # a genuinely translated clip. Spreading the dict means any
            # field added to rendered_clips in future flows through
            # automatically instead of needing a second edit here.
            merged = dict(clip)
            # The model supplies only descriptive copy; everything factual
            # stays owned by the render/telemetry records above.
            merged.update({
                "hook_title": meta.get("hook_title", "Autonomous Core Insight"),
                "social_caption": meta.get("social_caption", "Engineered by NornPulse"),
                "virality_score": meta.get("virality_score", 90.0),
                "hook_type": clip.get("hook_type", meta.get("hook_type", "unknown")),
                "is_top_tier_hook": clip.get("is_top_tier_hook", False),
            })
            # Where the footage came from. A published clip whose metadata
            # cannot answer "cut from what?" leaves the one question that
            # matters about a derived video — whose material is this? —
            # permanently unanswerable. The first clip this project
            # published has no source field, and there is now no way to
            # establish its provenance short of watching it.
            if source_ref:
                merged["source_url"] = source_ref
            final.append(merged)

        return final