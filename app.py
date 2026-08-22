# app.py
"""
⚡ NornPulse: Autonomous Short-Form Engine (ADK Native & Multimodal)
Built for Norn Labs (nornlabs.ai)
"""

import os
import json
import logging
import random
import pandas as pd
import plotly.express as px
import streamlit as st
from pathlib import Path
from dotenv import load_dotenv

from agent.verdandi_orchestrator import (
    VerdandiADK, filter_transcript_by_window, AUTO_WINDOW_MAX_SEC, BATCH_MAX_VIDEOS,
)
from agent.skuld_renderer import get_video_duration_seconds, format_seconds_to_mmss
from agent.norn_publisher import NornPublisher, PublishError
from agent import review_queue as rq
from agent import global_benchmarks as gb
from agent import trending_ingest as ti
from utils.ingest import download_youtube_video, list_playlist_video_urls, get_youtube_duration
from utils.transcribe import get_or_create_transcript
from config import Config

logger = logging.getLogger("nornpulse.app")
load_dotenv(override=True)

st.set_page_config(
    page_title="NornPulse: Autonomous Short-Form Engine",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Catamaran:wght@700;900&family=Cairo:wght@400;600&family=Biryani:wght@400;600&display=swap');

    html, body, [class*="css"] { font-family: 'Cairo', sans-serif; color: #e2e8f0; font-size: 1rem; }
    h1, h2, h3, h4 { font-family: 'Catamaran', sans-serif !important; letter-spacing: 0.3px; }
    p, span, label, div { font-family: 'Biryani', sans-serif; font-size: 0.98rem; }

    .stApp {
        background: radial-gradient(circle at 15% 0%, #131a2b 0%, #0b0f1a 45%, #060810 100%);
    }

    .main-title {
        font-family: 'Catamaran', sans-serif !important;
        font-weight: 900;
        font-size: clamp(3.4rem, 8vw, 6rem);
        line-height: 1.0;
        letter-spacing: -1px;
        background: linear-gradient(90deg, #00f2fe 0%, #4facfe 45%, #f093fb 100%);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        text-shadow: 0 0 56px rgba(79, 172, 254, 0.3);
        margin-bottom: -4px;
    }
    .sub-title {
        color: #94a3b8; font-size: 1.05rem; margin-bottom: 26px;
        letter-spacing: 0.6px; text-transform: uppercase; opacity: 0.85;
    }
    @keyframes norn-pulse {
        0%, 100% { transform: scale(1); opacity: 1; }
        50% { transform: scale(1.2); opacity: 0.65; }
    }
    .workflow-header {
        font-family: 'Catamaran', sans-serif; font-size: 1.2rem; font-weight: 700; color: #f8fafc;
        border-bottom: 1px solid rgba(255, 255, 255, 0.12); padding-bottom: 8px; margin-bottom: 14px;
        display: flex; align-items: center; gap: 8px;
    }
    div[data-testid="column"] > div {
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 16px;
        padding: 18px;
        backdrop-filter: blur(10px);
    }
    .stButton > button {
        border-radius: 10px !important;
        font-weight: 600 !important;
        transition: transform 0.15s ease, box-shadow 0.15s ease !important;
    }
    .stButton > button:hover {
        transform: translateY(-1px);
        box-shadow: 0 6px 18px rgba(79, 172, 254, 0.25);
    }
    div[data-baseweb="slider"] { padding-top: 6px; }

    /* Pipeline stepper: live per-agent progress during generation,
       replacing the old single generic loading banner. */
    .np-stepper { margin-bottom: 4px; }
    .np-stepper-pills { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
    .np-step {
        display: inline-flex; align-items: center; gap: 6px;
        padding: 6px 13px; border-radius: 999px; font-size: 0.85rem; font-weight: 600;
        font-family: 'Catamaran', sans-serif;
        border: 1px solid rgba(255, 255, 255, 0.1); transition: all 0.3s ease;
        white-space: nowrap;
    }
    .np-step-pending { opacity: 0.35; background: rgba(255, 255, 255, 0.03); }
    .np-step-done { opacity: 0.8; background: rgba(79, 172, 254, 0.14); border-color: rgba(79, 172, 254, 0.32); }
    .np-step-active {
        opacity: 1;
        background: linear-gradient(90deg, rgba(0, 242, 254, 0.28), rgba(240, 147, 251, 0.28));
        border-color: rgba(240, 147, 251, 0.55);
        animation: norn-pulse 1.3s ease-in-out infinite;
        box-shadow: 0 0 18px rgba(79, 172, 254, 0.3);
    }
    .np-stepper-message { font-size: 0.92rem; color: #94a3b8; font-style: italic; margin-bottom: 10px; }
</style>
""", unsafe_allow_html=True)

# Ordered stage keys emitted by VerdandiADK.orchestrate_generation's
# progress_callback (see agent/verdandi_orchestrator.py) -> short pill
# labels for the live pipeline stepper. Order here is purely the pill
# DISPLAY order, not an assumption about when each fires -- a multi-clip
# run revisits bragi/heimdall/mimir/skuld/urdr_log once per clip, which
# _render_pipeline_stepper handles by tracking "ever seen" rather than
# "furthest reached".
PIPELINE_STAGES = [
    ("urdr", "🔮 Urðr"),
    ("upload", "📤 Upload"),
    ("verdandi", "🧠 Verðandi"),
    ("bragi", "🎵 Bragi"),
    ("heimdall", "👁️ Heimdall"),
    ("mimir", "🗣️ Mímir"),
    ("skuld", "🎬 Skuld"),
    ("urdr_log", "📊 Log"),
]

# Batch mode does its own per-video download + transcription before
# handing off to the same inner pipeline, so it shows two extra leading
# pills. Single-video mode does that work in Column 1 instead, before
# generation is ever triggered, which is why it doesn't need them.
BATCH_PIPELINE_STAGES = [
    ("download", "⬇️ Download"),
    ("transcribe", "📝 Transcript"),
] + PIPELINE_STAGES


def _render_pipeline_stepper(active_stage: str, seen_stages: set, message: str, stages=None) -> str:
    pills = []
    for key, label in (stages or PIPELINE_STAGES):
        if key == active_stage:
            cls = "np-step-active"
        elif key in seen_stages:
            cls = "np-step-done"
        else:
            cls = "np-step-pending"
        pills.append(f"<span class='np-step {cls}'>{label}</span>")
    return (
        "<div class='np-stepper'>"
        f"<div class='np-stepper-pills'>{''.join(pills)}</div>"
        f"<div class='np-stepper-message'>{message}</div>"
        "</div>"
    )

LAST_SESSION_CACHE = Path(".nornpulse_last_session.json")


def _load_last_session() -> dict:
    if LAST_SESSION_CACHE.exists():
        try:
            return json.loads(LAST_SESSION_CACHE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_last_session(yt_url: str, transcript: str) -> None:
    try:
        LAST_SESSION_CACHE.write_text(
            json.dumps({"yt_url": yt_url, "transcript": transcript}, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"Could not persist last session cache: {e}")


# --- Cached ClickHouse reads for Tab 3 ---
# st.tabs() executes the code inside EVERY tab on EVERY rerun, regardless
# of which tab is visually active — so uncached queries here were firing
# on every unrelated interaction anywhere in the app (including
# continuously while dragging the Warmth/Crazy sliders). Defined at
# module scope (not inside the tab block) so they can be explicitly
# invalidated with .clear() right after actions that actually change the
# underlying data, rather than waiting out the TTL.
@st.cache_data(ttl=30, show_spinner=False)
def _cached_hook_benchmarks(_urdr):
    return _urdr.get_hook_type_benchmarks()


@st.cache_data(ttl=30, show_spinner=False)
def _cached_visual_benchmarks(_urdr):
    """All three visual dimensions in one ClickHouse round-trip."""
    return _urdr.get_all_visual_benchmarks()


@st.cache_data(ttl=15, show_spinner=False)
def _cached_published_outcomes(_urdr):
    return _urdr.get_published_outcomes()


@st.cache_data(ttl=60, show_spinner=False)
def _cached_topic_categories(_urdr):
    return _urdr.get_distinct_topic_categories()


# The global layer is materialised, not live — these are cheap local reads,
# but they're still ClickHouse round-trips inside a tab body that Streamlit
# executes on every rerun, so they cache like the rest.
@st.cache_data(ttl=600, show_spinner=False)
def _cached_global_facts():
    """
    The entire materialised facts table in one round-trip. It is a few
    dozen rows, and every ClickHouse call spawns its own mcp-clickhouse
    subprocess (~3s), so reading it per-accessor made Tab 3 crawl.
    """
    return gb.load_facts()


@st.cache_data(ttl=300, show_spinner=False)
def _cached_trending_tags(limit: int = 15):
    return ti.top_tags(limit=limit)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_trending_summary():
    return ti.snapshot_summary()


# Channel size drives every honest reading of the global data, so it is a
# setting rather than an assumption. Defaults to the smallest band, which
# is where a new NornPulse channel actually sits.
if "channel_subs" not in st.session_state:
    st.session_state.channel_subs = 0

if "verdandi_adk" not in st.session_state:
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "norn-labs-default")
    st.session_state.verdandi_adk = VerdandiADK(project_id=project_id)
if "publisher" not in st.session_state: st.session_state.publisher = NornPublisher()
if "current_generation" not in st.session_state: st.session_state.current_generation = []
if "published_count" not in st.session_state: st.session_state.published_count = 0
if "alignment_history" not in st.session_state: st.session_state.alignment_history = []
if "recently_published" not in st.session_state: st.session_state.recently_published = []

_last_session = _load_last_session()
if "yt_url" not in st.session_state:
    st.session_state.yt_url = _last_session.get("yt_url", "https://www.youtube.com/watch?v=tLPkpBN6bEI")
if "transcript_input" not in st.session_state:
    st.session_state.transcript_input = _last_session.get("transcript", "")
if "_transcript_source_video" not in st.session_state:
    # Tracks which video the current transcript_input was extracted from,
    # so re-extraction only happens when the video actually changes —
    # not on every unrelated rerun (slider move, Publish click, etc.).
    st.session_state._transcript_source_video = None

st.markdown("<h1 class='main-title'>⚡ NornPulse</h1>", unsafe_allow_html=True)
st.markdown(
    "<div class='sub-title'>Autonomous Short-Form Engine · Norn Labs (nornlabs.ai) · Multimodal Vision & ADK Native</div>",
    unsafe_allow_html=True,
)

# Global ClickHouse health banner, deliberately ABOVE the tabs so it's
# visible no matter which tab is open. Urðr degrades to in-memory
# fallback benchmarks when ClickHouse is unreachable, which keeps the app
# usable — but also makes a misconfigured instance look completely
# healthy while quietly serving synthetic data. That's a silent-wrong
# failure, and for the ClickHouse track it's the one state that must
# never go unnoticed, so it gets an unmissable banner with the actual
# reason and a retry rather than a small badge buried in Tab 3.
_urdr_health = st.session_state.verdandi_adk.urdr
if not _urdr_health.is_connected():
    st.error(
        "🔴 **ClickHouse is NOT connected** — Urðr is serving in-memory fallback "
        "benchmarks, so nothing you generate is grounded in (or logged to) real "
        "ClickHouse data.\n\n"
        f"**Reason:** {_urdr_health.connection_error or 'unknown'}"
    )
    if st.button("🔄 Retry ClickHouse Connection", key="retry_clickhouse"):
        with st.spinner("Reconnecting to ClickHouse via mcp-clickhouse..."):
            reconnected = _urdr_health.connect()
        if reconnected:
            # Fallback-mode results are now stale — drop them so the
            # charts repopulate from the real database.
            _cached_hook_benchmarks.clear()
            _cached_visual_benchmarks.clear()
            _cached_published_outcomes.clear()
            _cached_topic_categories.clear()
            st.rerun()
        else:
            st.warning("Still unable to reach ClickHouse — see the reason above.")

nav_tab1, nav_tab2, nav_tab3 = st.tabs(["🚀 Pipeline & Staging", "📚 Library & Archives", "📊 ClickHouse Analytics"])

# =========================================================================
# TAB 1: PIPELINE & STAGING WORKFLOW (3-Column Layout)
# =========================================================================
with nav_tab1:
    col_left, col_mid, col_right = st.columns(3, gap="medium")

    # --- COLUMN 1: Source Video & Ingestion ---
    with col_left:
        st.markdown("<div class='workflow-header'>1️⃣ Source Video Ingestion</div>", unsafe_allow_html=True)
        yt_url = st.text_input("YouTube Video Source:", key="yt_url")
        active_video_path = None

        if yt_url:
            # Check the video's real length BEFORE downloading anything —
            # a long video gets a bounded window picked here and only
            # THAT range is downloaded (yt-dlp download_ranges, confirmed
            # live: a 30s slice of a 94-min video downloaded in ~15s as
            # ~1.8MB instead of pulling the full ~180MB file). Auto-window
            # bounding what Verðandi reasons over doesn't help if the
            # whole file still has to be downloaded first.
            try:
                probed_duration = get_youtube_duration(yt_url)
            except Exception as e:
                probed_duration = None
                st.warning(f"Could not check video length ahead of download ({e}); downloading normally.")

            download_time_range = None
            if probed_duration and probed_duration > AUTO_WINDOW_MAX_SEC:
                window_pick = st.radio(
                    f"🎬 Long video (~{int(probed_duration // 60)} min) — pick a "
                    f"{int(AUTO_WINDOW_MAX_SEC // 60)}-min window to download:",
                    options=["Random", "From Start"], horizontal=True,
                    help="Only this window gets downloaded, not the whole video.",
                )
                window_start = (
                    random.uniform(0.0, probed_duration - AUTO_WINDOW_MAX_SEC)
                    if window_pick == "Random" else 0.0
                )
                download_time_range = (window_start, window_start + AUTO_WINDOW_MAX_SEC)

            @st.cache_data(show_spinner=True)
            def cached_download(url: str, time_range):
                return download_youtube_video(url, time_range=time_range)

            with st.spinner("Ingesting stream..."):
                try:
                    active_video_path = cached_download(yt_url, download_time_range)
                    if active_video_path and os.path.exists(active_video_path):
                        st.video(active_video_path)
                        if download_time_range:
                            st.caption(
                                f"✂️ Downloaded {format_seconds_to_mmss(download_time_range[0])}–"
                                f"{format_seconds_to_mmss(download_time_range[1])} of the full "
                                f"{format_seconds_to_mmss(probed_duration)} video."
                            )
                    else:
                        st.error("Downloaded video path is invalid.")
                except Exception as e:
                    st.error(f"Download failed: {e}")

        with st.expander(f"🗂️ Batch Mode (channel/playlist, up to {BATCH_MAX_VIDEOS} videos)"):
            st.caption(
                f"Runs the full pipeline once per video (capped at {BATCH_MAX_VIDEOS} — each is a real "
                "Gemini + Lyria + image + TTS generation), then ranks every resulting clip by predicted "
                "virality score in the Review & Publish column. Uses its own fixed style defaults rather "
                "than Column 2's sliders, since those aren't set yet at this point in the layout."
            )
            batch_url = st.text_input("YouTube channel or playlist URL:", key="batch_url")
            batch_content_hint = st.text_input(
                "🎬 Creative Direction (optional)", key="batch_content_hint",
                placeholder="e.g. a romantic moment, a tense confrontation...",
            ).strip() or None
            batch_caption_language = st.text_input(
                "🌐 Translate Captions (optional)", key="batch_caption_language",
                placeholder="e.g. English — leave blank to keep the source language",
            ).strip() or None
            if st.button("🗂️ Run Batch", key="run_batch"):
                if not batch_url:
                    st.error("Enter a channel or playlist URL first.")
                else:
                    with st.spinner(f"Enumerating up to {BATCH_MAX_VIDEOS} videos..."):
                        try:
                            batch_urls = list_playlist_video_urls(batch_url, max_videos=BATCH_MAX_VIDEOS)
                        except Exception as e:
                            batch_urls = []
                            st.error(f"Could not enumerate videos from that URL: {e}")
                    if batch_urls:
                        # Same live stepper as the single-video flow, with
                        # the two extra leading stages batch does per video.
                        # A batch is 3x the full pipeline, so this is where
                        # silent waiting hurt most.
                        batch_progress = st.empty()
                        batch_seen: set = set()

                        def _update_batch_progress(stage: str, message: str) -> None:
                            if stage != "done":
                                batch_seen.add(stage)
                            batch_progress.markdown(
                                _render_pipeline_stepper(
                                    stage, batch_seen, message, stages=BATCH_PIPELINE_STAGES,
                                ),
                                unsafe_allow_html=True,
                            )

                        _update_batch_progress(
                            "download", f"Queued — starting batch across {len(batch_urls)} video(s)...",
                        )
                        try:
                            batch_results = st.session_state.verdandi_adk.orchestrate_batch(
                                video_urls=batch_urls, target_count_per_video=1,
                                content_hint=batch_content_hint,
                                caption_language=batch_caption_language,
                                progress_callback=_update_batch_progress,
                            )
                            st.session_state.current_generation = batch_results
                            batch_progress.empty()
                            st.success(
                                f"✨ Batch complete: {len(batch_results)} clip(s) from "
                                f"{len(batch_urls)} video(s), ranked by virality score — see Review & Publish."
                            )
                        except Exception as e:
                            batch_progress.empty()
                            st.error(f"Batch run failed: {e}")

        if st.session_state.recently_published:
            st.markdown("<div class='workflow-header'>📤 Recently Published</div>", unsafe_allow_html=True)
            for pub in reversed(st.session_state.recently_published[-5:]):
                st.markdown(f"🔗 [{pub['title']}]({pub['url']}) · `{pub['privacy_status']}`")

    # --- COLUMN 2: Compact Transcript, Style, & Execution Controls ---
    with col_mid:
        st.markdown("<div class='workflow-header'>2️⃣ Compact Transcript & Controls</div>", unsafe_allow_html=True)

        if active_video_path and os.path.exists(active_video_path):
            @st.cache_data(show_spinner=True)
            def cached_transcript(video_path: str):
                return get_or_create_transcript(video_path)

            # Only re-extract when the video actually changed — not on
            # every rerun triggered by unrelated widgets (sliders, the
            # Publish button in Column 3, etc.). This also means manual
            # edits to the transcript below survive those reruns instead
            # of being silently overwritten.
            if st.session_state._transcript_source_video != active_video_path:
                with st.spinner("Extracting transcript..."):
                    try:
                        st.session_state.transcript_input = cached_transcript(active_video_path)
                        st.session_state._transcript_source_video = active_video_path
                    except Exception as e:
                        st.error(f"Transcription failed: {e}")

        transcript_input = st.text_area("Timestamped Transcript:", key="transcript_input", height=160)
        if not transcript_input.strip():
            st.caption(
                "🎥 No transcript — Verðandi will fall back to vision mode: Gemini watches the "
                "uploaded video directly (no burned-in captions, since there's no dialogue to caption). "
                "Works well for silent/instrumental sources; adds upload + processing latency."
            )

        target_clips = st.slider("Target Iteration Count", min_value=1, max_value=3, value=1)

        st.markdown("<div class='workflow-header'>🎨 Caption Style</div>", unsafe_allow_html=True)
        warmth = st.slider(
            "🌡️ Warmth", min_value=0.0, max_value=1.0, value=0.5, step=0.05,
            help="Cool blue/white captions at 0.0 → warm gold/orange color grade at 1.0",
        )
        crazy = st.slider(
            "⚡ Crazy", min_value=0.0, max_value=1.0, value=0.3, step=0.05,
            help="Controls both the reveal pace and the pop: ~5-word phrases with a gentle "
                 "bounce at 0.0 → rapid single-word pops with scale overshoot and wobble at 1.0.",
        )

        # Everything below shapes WHICH moment gets picked or steers a
        # secondary creative dimension, rather than being needed for every
        # run — tucked away so the always-visible controls above stay
        # scannable at a glance.
        transcript_window = None
        auto_window_mode = "random"
        with st.expander("⚙️ Advanced Settings"):
            if active_video_path and os.path.exists(active_video_path):
                @st.cache_data(show_spinner=False)
                def _cached_duration(video_path: str) -> float:
                    return get_video_duration_seconds(video_path)

                try:
                    video_duration_sec = _cached_duration(active_video_path)
                    window_choice = st.slider(
                        "✂️ Cut From/To (optional)",
                        min_value=0.0, max_value=float(video_duration_sec),
                        value=(0.0, float(video_duration_sec)), step=1.0,
                        help="Restrict generation to a portion of the video. Leave at the full range "
                             "to let Verðandi choose from the whole thing (default).",
                    )
                    is_narrowed = window_choice[0] > 0.5 or window_choice[1] < video_duration_sec - 0.5
                    if is_narrowed:
                        transcript_window = window_choice
                        scoped_transcript = filter_transcript_by_window(transcript_input, transcript_window)
                        line_count = len([ln for ln in scoped_transcript.strip().split("\n") if ln.strip()])
                        st.caption(
                            f"✂️ Scoped to {format_seconds_to_mmss(window_choice[0])}–"
                            f"{format_seconds_to_mmss(window_choice[1])} "
                            f"({line_count} transcript line{'s' if line_count != 1 else ''} in range, "
                            f"or vision mode within this window if none)."
                        )
                    # No "video is long, pick a window" toggle here anymore —
                    # Column 1 already handles that at download time, so the
                    # video reaching this point is already ≤ AUTO_WINDOW_MAX_SEC
                    # for the normal YouTube-URL flow. orchestrate_generation's
                    # own auto-window fallback (auto_window_mode, still passed
                    # below) stays as a defensive backstop for paths that don't
                    # go through Column 1's pre-trimmed download — it just
                    # won't fire here.
                except Exception as e:
                    logging.getLogger("nornpulse.app").warning(f"Could not read video duration for cut range slider: {e}")

            available_topics = _cached_topic_categories(st.session_state.verdandi_adk.urdr)
            topic_options = ["Auto (let Verðandi decide)"] + available_topics
            topic_choice = st.selectbox(
                "🎯 Topic Focus — ground generation in a specific topic category's history",
                topic_options, index=0,
                help="Scopes the ClickHouse retention data fed to Verðandi to one topic_category, "
                     "instead of the full historical spread. Falls back to all categories if the "
                     "chosen one has no matching history yet.",
            )
            topic_focus = None if topic_choice == topic_options[0] else topic_choice

            content_hint = st.text_input(
                "🎬 Creative Direction (optional)",
                key="content_hint",
                placeholder="e.g. a romantic moment, a tense confrontation, a funny reaction...",
                help="Free-text steer for WHICH moment gets picked. Verðandi prioritizes a genuine match "
                     "over a marginally higher virality score — leave blank to let it pick freely.",
            ).strip() or None

            caption_language = st.text_input(
                "🌐 Translate Captions (optional)",
                key="caption_language",
                placeholder="e.g. English, Spanish — leave blank to keep the source language",
                help="Burns in captions translated into this language instead of the source transcript's "
                     "own language. Timing is unaffected — only the on-screen words change. Verðandi's "
                     "reasoning and Mímir's narration fallback still use the original-language transcript.",
            ).strip() or None

            cut_energy = st.slider(
                "🎬 Cut Energy", min_value=0.0, max_value=1.0, value=0.5, step=0.05,
                help="Biases the target clip length within the duration range: calm at 0.0 leans "
                     "toward the longer end (let the moment breathe), energetic at 1.0 leans toward "
                     "the shorter end (snappy cut). A bias, not a hard override — the min/max range "
                     "itself is still always enforced.",
            )

        generate_clicked = st.button("⚡ EXECUTE PIPELINE", type="primary")

        if generate_clicked and not active_video_path:
            st.error("No video loaded — check the YouTube URL in Column 1.")
        elif generate_clicked and active_video_path:
            # Live per-agent progress instead of one generic banner: each
            # tool call inside orchestrate_generation's single blocking
            # Gemini turn (Bragi compose, Heimdall thumbnail, Mímir
            # narrate, Skuld render, ...) invokes progress_callback as
            # ordinary synchronous Python — Streamlit flushes each
            # placeholder.markdown() to the browser immediately, so this
            # updates live with no threading/polling needed.
            progress_placeholder = st.empty()
            seen_stages: set = set()

            def _update_progress(stage: str, message: str) -> None:
                if stage != "done":
                    seen_stages.add(stage)
                progress_placeholder.markdown(
                    _render_pipeline_stepper(stage, seen_stages, message), unsafe_allow_html=True,
                )

            _update_progress("urdr", "Queued — starting the Norns...")
            try:
                final_metadata = st.session_state.verdandi_adk.orchestrate_generation(
                    transcript_text=transcript_input,
                    video_path=active_video_path,
                    target_count=target_clips,
                    warmth=warmth,
                    crazy=crazy,
                    topic_focus=topic_focus,
                    cut_energy=cut_energy,
                    transcript_window=transcript_window,
                    auto_window_mode=auto_window_mode,
                    content_hint=content_hint,
                    caption_language=caption_language,
                    progress_callback=_update_progress,
                )

                output_dir = Path("output_clips")
                output_dir.mkdir(parents=True, exist_ok=True)
                for meta in final_metadata:
                    clip_id = meta.get("clip_id", "clip_default")
                    with open(output_dir / f"{clip_id}_metadata.json", "w", encoding="utf-8") as f:
                        json.dump(meta, f, indent=2)

                st.session_state.alignment_history.extend(
                    [bool(m.get("is_top_tier_hook")) for m in final_metadata]
                )

                # New clips were just inserted into video_hook_retention —
                # force Tab 3's benchmark charts to reflect them immediately
                # instead of waiting out the cache TTL.
                _cached_hook_benchmarks.clear()
                _cached_visual_benchmarks.clear()

                _save_last_session(yt_url, transcript_input)
                st.session_state.current_generation = final_metadata
                progress_placeholder.empty()
                st.success("✨ Execution complete!")
            except Exception as e:
                progress_placeholder.empty()
                st.error(f"Pipeline execution failed: {e}")

    # --- COLUMN 3: Generated Output Preview & Publishing ---
    with col_right:
        st.markdown("<div class='workflow-header'>3️⃣ Review & Publish</div>", unsafe_allow_html=True)

        if not st.session_state.current_generation:
            st.info("No active generated shorts to review yet. Run the pipeline from column 2.")
        else:
            output_dir = Path("output_clips")

            @st.cache_data(show_spinner=False, ttl=300)
            def _cached_similar_shorts(_urdr, hook_type: str):
                return _urdr.query_hook_retention(hook_category=hook_type, limit=3)

            for idx, item in enumerate(st.session_state.current_generation):
                c_id = item.get("clip_id")
                c_path = output_dir / f"{c_id}_9x16.mp4"

                if c_path.exists():
                    # Constrained to a phone-shaped preview width — a 9:16
                    # video rendered at full column width (like the 16:9
                    # source clip in Column 1) looks disproportionately huge.
                    vid_col, thumb_col = st.columns([2, 1])
                    with vid_col:
                        st.video(str(c_path), width=280)
                    thumbnail_path = item.get("thumbnail_path")
                    if thumbnail_path and Path(thumbnail_path).exists():
                        with thumb_col:
                            st.image(thumbnail_path, width=90, caption="👁️ Heimdall cover")
                # The 0-100 virality score is Verðandi's internal ranking and
                # has no external referent — it says nothing about what this
                # clip might actually get. The forecast beside it is grounded
                # in what comparable real videos did.
                score_col, reach_col = st.columns(2)
                with score_col:
                    st.metric("Virality Score", f"{item.get('virality_score', 90.0)}/100",
                              help="Verðandi's internal ranking of this clip against the "
                                   "others it generated. Relative, not predictive.")

                forecast = gb.forecast_reach(
                    st.session_state.channel_subs,
                    has_subtitles=bool(item.get("has_subtitles")),
                    upload_day=st.session_state.get("planned_upload_day") or None,
                    facts=_cached_global_facts(),
                )
                with reach_col:
                    if forecast:
                        st.metric(
                            "Forecast reach (p50)", f"{forecast['p50']:,.0f} views",
                            help=f"Median outcome for comparable videos from a "
                                 f"{forecast['size_band']}-subscriber channel.",
                        )
                    else:
                        st.metric("Forecast reach (p50)", "—",
                                  help="Run seed_global_benchmarks.py to materialise "
                                       "the global facts this is derived from.")

                if forecast:
                    st.caption(
                        f"　📊 Plausible range **{forecast['p10']:,.0f} – {forecast['p90']:,.0f}** views "
                        f"(p10–p90), centred on {forecast['p50']:,.0f}."
                    )
                    with st.expander("How this forecast is derived"):
                        for comp in forecast["components"]:
                            flag = "" if comp["banded"] else "  ⚠️ not size-banded"
                            st.markdown(
                                f"- **{comp['factor']}** — {comp['detail']} "
                                f"(×{comp['multiplier']:.2f}, {comp['basis']}){flag}"
                            )
                        st.caption(
                            "Read as *comparable videos got this much*, not *this clip will*. "
                            "Every factor is correlational, the weekday factor isn't stratified "
                            "by channel size, and nothing here looks at the clip's actual content."
                        )
                if item.get("has_subtitles"):
                    caption_lang = item.get("caption_language")
                    st.caption(
                        f"💬 Kinetic subtitles burned in — translated to {caption_lang}" if caption_lang
                        else "💬 Kinetic subtitles burned in"
                    )
                    # Evidence sits next to the decision it justifies. Read
                    # within this channel's size band, never across all of
                    # YouTube: captioned videos skew to large channels, so
                    # the unstratified comparison measures audience, not
                    # captioning.
                    lift = gb.subtitle_lift(
                        gb.size_band_for(st.session_state.channel_subs),
                        facts=_cached_global_facts(),
                    )
                    if lift:
                        views_txt = (f"{lift['views_lift_pct']:+.0f}% median views"
                                     if abs(lift["views_lift_pct"]) >= 1 else "no measurable view lift")
                        like_txt = (f", {lift['like_lift_pct']:+.0f}% like rate"
                                    if lift["like_lift_pct"] is not None else "")
                        st.caption(
                            f"　↳ 🌍 {views_txt}{like_txt} for {lift['size_band']}-subscriber "
                            f"channels ({lift['sample_videos']:,} real videos)"
                        )
                if item.get("has_bragi_score"):
                    genre = item.get("music_genre") or "custom"
                    mood = item.get("music_mood") or ""
                    st.caption(f"🎵 Original score by Bragi (Lyria) — {genre}, {mood}".rstrip(", "))
                if item.get("has_narration"):
                    st.caption("🗣️ AI narration by Mímir (fills silence, or reads over hard-to-hear audio)")

                hook_type = item.get("hook_type", "unknown")
                top_hook = item.get("grounded_top_hook_type", "—")
                hook_rank = item.get("hook_rank")
                if item.get("is_top_tier_hook"):
                    st.success(f"✅ Grounded pick: **{hook_type}** (Urðr's #{hook_rank} ranked hook)")
                elif hook_rank is not None:
                    st.warning(f"⚠️ **{hook_type}** ranks #{hook_rank} in Urðr's benchmarks — top pick was **{top_hook}**")
                else:
                    st.caption(f"Hook type: {hook_type} (not found in Urðr's benchmark taxonomy)")

                similar_df = _cached_similar_shorts(st.session_state.verdandi_adk.urdr, hook_type)
                if not similar_df.empty:
                    with st.expander(f"📊 Similar historical '{hook_type}' shorts"):
                        st.dataframe(
                            similar_df[["hook_text", "virality_score", "avg_3s_retention_pct", "completion_rate_pct", "sample_size_views"]],
                            width='stretch', hide_index=True,
                        )

                t_val = st.text_input("Title", value=item.get("title", f"{item.get('hook_title')} #Shorts"), key=f"t_{c_id}")
                d_val = st.text_area("Description", value=item.get("social_caption", ""), height=50, key=f"d_{c_id}")
                privacy_choice = st.selectbox(
                    "Visibility", ["private", "unlisted", "public"], index=0, key=f"privacy_{c_id}",
                    help="Private: only accounts you explicitly add as viewers in YouTube Studio can see it — the closest YouTube has to internal testing.",
                )

                # A rejection without a reason teaches nothing; the comment
                # is recorded on both paths and mirrored to ClickHouse so
                # rejections can later be correlated against hook types
                # and visual treatments.
                comment = st.text_area(
                    "Review comment", value="", height=68, key=f"cmt_{c_id}",
                    placeholder="Why this works, or why it doesn't — recorded with either decision.",
                )

                prior = rq.get_decision(c_id)
                if prior:
                    st.caption(
                        f"↩️ Previously **{prior['status']}** via {prior.get('source', '?')} "
                        f"on {prior.get('decided_at', '?')}"
                        + (f" — “{prior['comment']}”" if prior.get("comment") else "")
                    )

                st.selectbox(
                    "Planned upload day", ["", "Monday", "Tuesday", "Wednesday", "Thursday",
                                           "Friday", "Saturday", "Sunday"],
                    key="planned_upload_day",
                    help="Feeds the reach forecast. Weekend uploads show materially higher "
                         "reach per subscriber in the global data.",
                )

                b1, b2 = st.columns(2, gap="small")
                with b1:
                    if st.button("🚀 Publish", key=f"pub_{c_id}", type="primary"):
                        with st.spinner("Publishing..."):
                            try:
                                result = st.session_state.publisher.upload_to_youtube_shorts(
                                    c_path, t_val, d_val, privacy_status=privacy_choice,
                                    thumbnail_path=item.get("thumbnail_path"),
                                )

                                # Log the prediction-side row now, so Tab 3's
                                # cross-validation panel has something to
                                # compare real stats against once synced.
                                urdr = st.session_state.verdandi_adk.urdr
                                benchmark_df = urdr.query_hook_retention(hook_category=hook_type, limit=1)
                                predicted_3s = (
                                    float(benchmark_df.iloc[0]["avg_3s_retention_pct"])
                                    if not benchmark_df.empty else 85.0
                                )
                                # Store the forecast made *before* publishing,
                                # in the same units as actual_view_count, so
                                # the cross-validation is like-for-like.
                                urdr.log_published_outcome(
                                    clip_id=c_id,
                                    youtube_video_id=result["video_id"],
                                    youtube_url=result["url"],
                                    hook_type=hook_type,
                                    predicted_virality_score=item.get("virality_score", 90.0),
                                    predicted_3s_retention_pct=predicted_3s,
                                    forecast_views_p50=(forecast or {}).get("p50", 0.0),
                                    forecast_views_p90=(forecast or {}).get("p90", 0.0),
                                )

                                st.session_state.recently_published.append({
                                    "clip_id": c_id,
                                    "title": t_val,
                                    "url": result["url"],
                                    "privacy_status": result["privacy_status"],
                                })
                                thumb_note = " · 👁️ custom thumbnail set" if result.get("thumbnail_set") else ""
                                st.success(f"✨ Published: [{result['url']}]({result['url']}) · {result['privacy_status']}{thumb_note}")
                                st.session_state.published_count += 1
                                _cached_published_outcomes.clear()
                                rq.record_decision(
                                    c_id, rq.APPROVED, comment, source="ui",
                                    extra={"youtube_url": result["url"],
                                           "youtube_video_id": result["video_id"]},
                                )
                                # Archive rather than unlink: the local copy
                                # of a clip that just went live used to be
                                # deleted outright, so there was no way to
                                # re-check what had actually been published.
                                rq.archive_published(c_id)
                                st.session_state.current_generation.pop(idx)
                                st.rerun()
                            except PublishError as e:
                                st.error(f"❌ Publish failed: {e}")
                with b2:
                    if st.button("🗑️ Reject", key=f"rej_{c_id}"):
                        rq.record_decision(c_id, rq.REJECTED, comment, source="ui")
                        moved = rq.archive_rejected(c_id)
                        st.session_state.current_generation.pop(idx)
                        st.warning(
                            f"Rejected — {len(moved)} file(s) archived to output_clips/rejected/."
                            + (" Comment recorded." if comment.strip() else "")
                        )
                        st.rerun()

# =========================================================================
# TAB 2: LIBRARY
# =========================================================================
with nav_tab2:
    st.markdown("<div class='workflow-header'>📚 Generated & Archived Shorts Library</div>", unsafe_allow_html=True)
    lib_dir = Path("output_clips")
    lib_files = list(lib_dir.glob("*_9x16.mp4")) if lib_dir.exists() else []

    if not lib_files: st.info("Library archive is empty.")
    else:
        for lib_path in lib_files:
            col_l1, col_l2 = st.columns([1, 2], gap="small")
            with col_l1: st.video(str(lib_path))
            with col_l2:
                st.markdown(f"**Asset:** `{lib_path.stem}.mp4`")
                if st.button("🗑️ Delete from Archive", key=f"del_{lib_path.stem}"):
                    lib_path.unlink(missing_ok=True)
                    st.rerun()
            st.divider()

# =========================================================================
# TAB 3: LIVE CLICKHOUSE ANALYTICS + PREDICTED-VS-ACTUAL CROSS-VALIDATION
# =========================================================================
with nav_tab3:
    st.markdown("<div class='workflow-header'>📊 Live ClickHouse Analytics Hub</div>", unsafe_allow_html=True)

    urdr = st.session_state.verdandi_adk.urdr
    connected = urdr.is_connected()

    if not connected:
        # The global banner above the tabs already carries the reason and
        # the retry; this is the local reminder that every chart below is
        # synthetic fallback data rather than anything real.
        st.error(
            "🔴 **Every chart on this tab is in-memory fallback data, not real ClickHouse data.** "
            "See the reason and retry at the top of the page."
        )

    benchmarks_df = _cached_hook_benchmarks(urdr)

    col_a1, col_a2, col_a3, col_a4, col_a5 = st.columns(5)
    with col_a1: st.metric("ADK Reasoning Engine", "Active 🟢")
    with col_a2: st.metric("Published Shorts", st.session_state.published_count)
    with col_a3:
        avg_retention = float(benchmarks_df["avg_3s_retention"].mean()) if not benchmarks_df.empty else 0.0
        st.metric("Avg. 3s Retention", f"{avg_retention:.1f}%")
    with col_a4:
        st.metric("ClickHouse State", "Connected 🟢" if connected else "Fallback 🟡")
    with col_a5:
        history = st.session_state.alignment_history
        alignment_rate = (100 * sum(history) / len(history)) if history else None
        st.metric(
            "Grounding Alignment",
            f"{alignment_rate:.0f}%" if alignment_rate is not None else "—",
            help="Share of generated clips where Verðandi's chosen hook_type ranked in Urðr's top 2 benchmarks.",
        )

    if not benchmarks_df.empty:
        fig = px.bar(
            benchmarks_df,
            x="hook_type",
            y=["avg_3s_retention", "avg_completion_rate"],
            barmode="group",
            template="plotly_dark",
            labels={"value": "Percent", "hook_type": "Hook Type", "variable": "Metric"},
        )
        st.plotly_chart(fig, width='stretch')

        # Retention drop-off curve: the README describes Urðr tracking
        # 3s/15s/30s drop-off curves per hook type — this is the first
        # place that data is actually visualized.
        curve_df = benchmarks_df.melt(
            id_vars=["hook_type"],
            value_vars=["avg_3s_retention", "avg_15s_retention", "avg_30s_retention"],
            var_name="checkpoint", value_name="retention_pct",
        )
        checkpoint_seconds = {"avg_3s_retention": 3, "avg_15s_retention": 15, "avg_30s_retention": 30}
        curve_df["seconds"] = curve_df["checkpoint"].map(checkpoint_seconds)
        curve_fig = px.line(
            curve_df.sort_values("seconds"),
            x="seconds", y="retention_pct", color="hook_type",
            markers=True, template="plotly_dark",
            title="Retention Drop-Off Curves by Hook Type",
            labels={"seconds": "Seconds into clip", "retention_pct": "Retention %"},
        )
        st.plotly_chart(curve_fig, width='stretch')

        # Observed performance per visual dimension. All three treatments
        # Skuld applies (framing, camera motion, color grade) are now
        # logged per clip, so each gets its own tab here. Each starts as a
        # single 'unknown' bucket — seed data and rows logged before the
        # column existed — and fills in as clips accumulate across
        # differing hook types, since the treatment is derived from
        # hook_type via visual_style_benchmarks.
        # ------------------------------------------------------------------
        # Global grounding: the two layers that aren't ours.
        # ------------------------------------------------------------------
        st.markdown("<div class='workflow-header'>🌍 Global YouTube Grounding</div>", unsafe_allow_html=True)
        st.caption(
            "Three layers live in this warehouse: **global structural facts** materialised from "
            "ClickHouse's public 4.56-billion-row YouTube dataset, a **current trending** snapshot "
            "pulled from the YouTube Data API, and **your own published clips**. The seed benchmarks "
            "above are priors the pipeline chooses from; this is external evidence about whether "
            "those choices are right."
        )

        subs = st.number_input(
            "Your channel's subscriber count", min_value=0, step=10,
            value=int(st.session_state.channel_subs),
            help="Channel size is the dominant confounder in the global data — captioned and "
                 "age-restricted videos skew heavily toward large channels. Every figure below is "
                 "read within the band this number falls into.",
        )
        st.session_state.channel_subs = int(subs)
        band = gb.size_band_for(int(subs))

        facts = _cached_global_facts()
        reach = gb.expected_reach(int(subs), facts=facts)
        lift = gb.subtitle_lift(band, facts=facts)
        days = gb.best_upload_days(size_band=band, facts=facts)

        m1, m2, m3 = st.columns(3)
        with m1:
            st.metric(f"Median reach · {band} subs",
                      f"{reach['median_views']:,.0f} views" if reach else "—",
                      help="What a typical video from a channel this size actually got. This is the "
                           "referent an abstract 0-100 virality score is missing.")
        with m2:
            st.metric("Subtitles → like rate",
                      f"{lift['like_lift_pct']:+.0f}%" if lift and lift["like_lift_pct"] is not None else "—",
                      help="Measured within your size band.")
        with m3:
            st.metric("Best upload day",
                      days[0]["day"] if days else "—",
                      help=f"Highest median views for {band}-subscriber channels. "
                           f"Ranked within the band: across all channel sizes the "
                           f"two available metrics disagree, because weekend uploads "
                           f"skew toward small channels.")

        if lift:
            direction = ("does **not** buy reach at this channel size — but it lifts engagement sharply"
                         if lift["views_lift_pct"] < 1 else "lifts both reach and engagement")
            st.info(
                f"**Captioning {direction}.** For {band}-subscriber channels, captioned videos get a "
                f"median {lift['median_views_with']:,.0f} views vs {lift['median_views_without']:,.0f} "
                f"({lift['views_lift_pct']:+.1f}%), with a {lift['like_lift_pct']:+.0f}% like rate — "
                f"across {lift['sample_videos']:,} real videos. Read across all channel sizes this "
                f"reverses, which is why it's banded."
            )

        g1, g2 = st.columns(2)
        with g1:
            reach_df = facts[facts["dimension"] == "channel_size_band"] if not facts.empty else facts
            if not reach_df.empty:
                order = ["0-100", "100-1k", "1k-10k", "10k-100k", "100k-1M", "1M+"]
                reach_df = reach_df[reach_df["bucket"].isin(order)].copy()
                reach_df["bucket"] = pd.Categorical(reach_df["bucket"], order, ordered=True)
                fig = px.bar(
                    reach_df.sort_values("bucket"), x="bucket", y="median_views",
                    template="plotly_dark", log_y=True,
                    title="Median reach by channel size (4.56B-row dataset)",
                    labels={"bucket": "Subscribers", "median_views": "Median views (log)"},
                )
                fig.add_vline(x=order.index(band), line_dash="dash", line_color="#00E5FF")
                st.plotly_chart(fig, width='stretch')

        with g2:
            # Must be filtered to one band: upload_weekday is stratified, so
            # the unfiltered frame stacks six size bands into every bar. And
            # it plots median views, not views-per-subscriber, so the chart
            # agrees with the "Best upload day" metric and the forecast
            # multiplier — those three disagreed while this read views/sub.
            wd = gb.weekday_facts(band, facts)
            if not wd.empty:
                names = {"1": "Mon", "2": "Tue", "3": "Wed", "4": "Thu",
                         "5": "Fri", "6": "Sat", "7": "Sun"}
                wd = wd.copy()
                wd["day"] = wd["bucket"].astype(str).map(names)
                wd["day"] = pd.Categorical(wd["day"], list(names.values()), ordered=True)
                fig = px.bar(
                    wd.sort_values("day"), x="day", y="median_views",
                    template="plotly_dark",
                    title=f"Median views by upload day · {band} subscribers",
                    labels={"day": "", "median_views": "Median views"},
                )
                st.plotly_chart(fig, width='stretch')
                st.caption(
                    "Within a size band the upload day barely matters — the large "
                    "weekend effect visible across all of YouTube is a channel-size "
                    "artifact, since weekend uploads skew toward small channels."
                )

        # --- current trending layer ---
        summary = _cached_trending_summary()
        if summary:
            st.markdown("**📈 Trending right now** — YouTube Data API, "
                        f"snapshot {summary['snapshot_at']}")
            t1, t2, t3 = st.columns(3)
            t1.metric("Videos in snapshot", f"{summary['videos']:,}")
            t2.metric("Actual Shorts (≤60s)", f"{summary['shorts']:,}",
                      help="The public dataset has no duration column, so it cannot separate "
                           "Shorts from long-form. The API can.")
            t3.metric("Median views", f"{summary['median_views']:,.0f}")

            tags = _cached_trending_tags(15)
            if not tags.empty:
                st.caption(
                    "Tags carried by currently-trending videos — real hashtags in circulation now, "
                    "rather than invented ones."
                )
                st.dataframe(tags, width='stretch', hide_index=True)
        else:
            st.caption(
                "No trending snapshot stored yet. Run `python ingest_trending.py --regions US,GB` "
                "to add the current layer (1 API quota unit per region)."
            )

        st.caption(
            "⚠️ Scope: the public dataset was crawled 27 Nov – 13 Dec 2021, so its view counts are "
            "frozen at that date and it predates mature Shorts behaviour. It carries no duration "
            "column, so nothing here is a Shorts-specific benchmark — that is what the trending "
            "layer above is for. Figures are 1/N sampled; sample sizes are shown throughout."
        )

        st.markdown("<div class='workflow-header'>🎬 Visual Treatment Performance</div>", unsafe_allow_html=True)
        st.caption(
            "What actually happened to generated clips, per visual dimension — as opposed to "
            "`visual_style_benchmarks`, which is the prior Skuld *chooses* from. Comparing the two "
            "is how you tell whether the grounded choice is paying off."
        )
        _visual_frames = _cached_visual_benchmarks(urdr)
        _dim_tabs = st.tabs(["✂️ Crop Mode", "🎥 Camera Motion", "🎨 Color Grade"])
        for _tab, (_dim, _label) in zip(
            _dim_tabs,
            [("crop_mode", "Crop Mode"), ("motion_effect", "Camera Motion"), ("color_grade", "Color Grade")],
        ):
            with _tab:
                _df = _visual_frames.get(_dim, pd.DataFrame())
                if _df.empty:
                    st.caption(f"No {_label.lower()} data recorded yet.")
                elif len(_df) > 1:
                    st.plotly_chart(
                        px.bar(
                            _df, x=_dim,
                            y=["avg_3s_retention", "avg_completion_rate", "avg_virality_score"],
                            barmode="group", template="plotly_dark",
                            labels={"value": "Score / Percent", _dim: _label, "variable": "Metric"},
                        ),
                        width='stretch',
                    )
                else:
                    st.caption(
                        f"{_label} data is all '{_df.iloc[0][_dim]}' so far — generate clips across "
                        f"different hook types to build a comparison."
                    )
    else:
        st.info("No benchmark data available yet.")

    st.markdown("<div class='workflow-header'>🎯 Predicted vs. Actual (YouTube Cross-Validation)</div>", unsafe_allow_html=True)

    outcomes_df = _cached_published_outcomes(urdr)
    if outcomes_df.empty:
        st.info("Publish a short from Tab 1 to start collecting real outcomes here.")
    else:
        if st.button("🔄 Sync Actual Performance"):
            with st.spinner("Pulling live stats from YouTube..."):
                synced, failed = 0, 0
                for video_id in outcomes_df["youtube_video_id"].unique():
                    try:
                        stats = st.session_state.publisher.get_video_statistics(video_id)
                        urdr.sync_actual_stats(
                            youtube_video_id=video_id,
                            view_count=stats["view_count"],
                            like_count=stats["like_count"],
                            comment_count=stats["comment_count"],
                        )
                        synced += 1
                    except PublishError as e:
                        logger.warning(f"Could not sync {video_id}: {e}")
                        failed += 1
                st.success(f"Synced {synced} video(s)." + (f" {failed} failed — check logs." if failed else ""))
                _cached_published_outcomes.clear()
                st.rerun()

        # Predicted vs. actual scatter — the real "does grounding work"
        # story: does a higher predicted_virality_score actually correlate
        # with more real views? Only meaningful once views are non-zero.
        synced_df = outcomes_df[outcomes_df["actual_view_count"] > 0]
        if not synced_df.empty:
            scatter_fig = px.scatter(
                synced_df, x="predicted_virality_score", y="actual_view_count",
                color="hook_type", size="actual_view_count", hover_data=["clip_id"],
                template="plotly_dark", title="Predicted Virality vs. Actual Views",
                labels={"predicted_virality_score": "Predicted Virality Score", "actual_view_count": "Actual Views"},
            )
            st.plotly_chart(scatter_fig, width='stretch')
        else:
            st.caption("Sync actual performance above once your published clips have real view counts to compare against.")

        # Forecast vs. actual. This is the comparison that can actually be
        # right or wrong: predicted_virality_score is a 0-100 internal
        # ranking, so plotting it against view counts only ever shows
        # whether the ordering held. The forecast is in views, so it can be
        # checked against the diagonal.
        forecast_df = outcomes_df[
            (outcomes_df.get("forecast_views_p50", pd.Series(dtype=float)) > 0)
            & (outcomes_df["actual_view_count"] > 0)
        ] if "forecast_views_p50" in outcomes_df.columns else pd.DataFrame()

        if not forecast_df.empty:
            fig = px.scatter(
                forecast_df, x="forecast_views_p50", y="actual_view_count",
                color="hook_type", hover_data=["clip_id", "forecast_views_p90"],
                template="plotly_dark", log_x=True, log_y=True,
                title="Grounded Forecast vs. Actual Views",
                labels={"forecast_views_p50": "Forecast views (p50, global data)",
                        "actual_view_count": "Actual views"},
            )
            lo = float(min(forecast_df["forecast_views_p50"].min(),
                           forecast_df["actual_view_count"].min()))
            hi = float(max(forecast_df["forecast_views_p50"].max(),
                           forecast_df["actual_view_count"].max()))
            fig.add_shape(type="line", x0=lo, y0=lo, x1=hi, y1=hi,
                          line=dict(dash="dash", color="#00E5FF"))
            st.plotly_chart(fig, width='stretch')
            st.caption(
                "Points on the dashed line landed exactly where the global data said "
                "comparable videos land. Above it, the clip beat its cohort."
            )
        elif "forecast_views_p50" in outcomes_df.columns:
            st.caption(
                "No clip has both a stored forecast and real views yet — forecasts are "
                "recorded from the next publish onward."
            )

        display_df = outcomes_df.copy()
        display_df["youtube_url"] = display_df["youtube_url"]
        st.dataframe(
            display_df[[
                c for c in [
                    "clip_id", "hook_type", "predicted_virality_score",
                    "predicted_3s_retention_pct", "forecast_views_p50",
                    "actual_view_count", "actual_like_count", "actual_comment_count",
                    "youtube_url", "last_synced_at",
                ] if c in display_df.columns
            ]],
            width='stretch',
            column_config={
                "youtube_url": st.column_config.LinkColumn("Video"),
            },
        )

    with st.expander("🔎 SQL Query Console"):
        default_query = (
            "SELECT hook_type, avg(virality_score) AS avg_virality\n"
            "FROM video_hook_retention\n"
            "GROUP BY hook_type\n"
            "ORDER BY avg_virality DESC"
        )
        user_query = st.text_area("Run a custom ClickHouse query:", value=default_query, height=90)
        if st.button("▶️ Execute Query"):
            try:
                result_df = urdr.execute_custom_query(user_query)
                st.dataframe(result_df, width='stretch')
            except Exception as e:
                st.error(f"Query failed: {e}")