# app.py
"""
⚡ NornPulse: Autonomous Short-Form Engine (ADK Native & Multimodal)
Built for Norn Labs (nornlabs.ai)
"""

import os
import json
import logging
import pandas as pd
import plotly.express as px
import streamlit as st
from pathlib import Path
from dotenv import load_dotenv

from agent.verdandi_orchestrator import VerdandiADK, filter_transcript_by_window
from agent.skuld_renderer import get_video_duration_seconds, format_seconds_to_mmss
from agent.norn_publisher import NornPublisher, PublishError
from utils.ingest import download_youtube_video
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
    @keyframes norn-shimmer {
        0% { background-position: -400px 0; }
        100% { background-position: 400px 0; }
    }
    @keyframes norn-pulse {
        0%, 100% { transform: scale(1); opacity: 1; }
        50% { transform: scale(1.2); opacity: 0.65; }
    }
    .norn-loading-banner {
        background: linear-gradient(90deg, rgba(0,242,254,0.12) 25%, rgba(240,147,251,0.3) 50%, rgba(0,242,254,0.12) 75%);
        background-size: 800px 100%;
        animation: norn-shimmer 1.8s linear infinite;
        border-radius: 12px;
        padding: 16px 20px;
        text-align: center;
        font-family: 'Catamaran', sans-serif;
        font-weight: 700;
        font-size: 1.05rem;
        border: 1px solid rgba(255, 255, 255, 0.12);
        margin-bottom: 12px;
    }
    .norn-loading-rune {
        display: inline-block;
        animation: norn-pulse 1.2s ease-in-out infinite;
        margin-right: 8px;
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
</style>
""", unsafe_allow_html=True)

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
def _cached_crop_benchmarks(_urdr):
    return _urdr.get_crop_mode_benchmarks()


@st.cache_data(ttl=15, show_spinner=False)
def _cached_published_outcomes(_urdr):
    return _urdr.get_published_outcomes()


@st.cache_data(ttl=60, show_spinner=False)
def _cached_topic_categories(_urdr):
    return _urdr.get_distinct_topic_categories()


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
            @st.cache_data(show_spinner=True)
            def cached_download(url: str):
                return download_youtube_video(url)

            with st.spinner("Ingesting stream..."):
                try:
                    active_video_path = cached_download(yt_url)
                    if active_video_path and os.path.exists(active_video_path):
                        st.video(active_video_path)
                    else:
                        st.error("Downloaded video path is invalid.")
                except Exception as e:
                    st.error(f"Download failed: {e}")

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

        transcript_window = None
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
            except Exception as e:
                logging.getLogger("nornpulse.app").warning(f"Could not read video duration for cut range slider: {e}")

        target_clips = st.slider("Target Iteration Count", min_value=1, max_value=3, value=1)

        st.markdown("<div class='workflow-header'>🎯 Topic Focus</div>", unsafe_allow_html=True)
        available_topics = _cached_topic_categories(st.session_state.verdandi_adk.urdr)
        topic_options = ["Auto (let Verðandi decide)"] + available_topics
        topic_choice = st.selectbox(
            "Ground generation in a specific topic category's history",
            topic_options, index=0,
            help="Scopes the ClickHouse retention data fed to Verðandi to one topic_category, "
                 "instead of the full historical spread. Falls back to all categories if the "
                 "chosen one has no matching history yet.",
        )
        topic_focus = None if topic_choice == topic_options[0] else topic_choice

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

        st.markdown("<div class='workflow-header'>✂️ Cut Style</div>", unsafe_allow_html=True)
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
            loading_placeholder = st.empty()
            loading_banner_text = (
                "Verðandi is watching your video directly (vision mode) — grounding in Urðr, "
                "rendering via Skuld..."
                if not transcript_input.strip()
                else "Verðandi is weaving your short — grounding in Urðr, rendering via Skuld..."
            )
            loading_placeholder.markdown(
                "<div class='norn-loading-banner'>"
                "<span class='norn-loading-rune'>⚡</span>"
                f"{loading_banner_text}"
                "</div>",
                unsafe_allow_html=True,
            )
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
                _cached_crop_benchmarks.clear()

                _save_last_session(yt_url, transcript_input)
                st.session_state.current_generation = final_metadata
                loading_placeholder.empty()
                st.success("✨ Execution complete!")
            except Exception as e:
                loading_placeholder.empty()
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
                st.metric("Virality Score", f"{item.get('virality_score', 90.0)}/100")
                if item.get("has_subtitles"):
                    st.caption("💬 Kinetic subtitles burned in")
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
                                urdr.log_published_outcome(
                                    clip_id=c_id,
                                    youtube_video_id=result["video_id"],
                                    youtube_url=result["url"],
                                    hook_type=hook_type,
                                    predicted_virality_score=item.get("virality_score", 90.0),
                                    predicted_3s_retention_pct=predicted_3s,
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
                                c_path.unlink(missing_ok=True)
                                st.session_state.current_generation.pop(idx)
                                st.rerun()
                            except PublishError as e:
                                st.error(f"❌ Publish failed: {e}")
                with b2:
                    if st.button("🗑️ Reject", key=f"rej_{c_id}"):
                        c_path.unlink(missing_ok=True)
                        st.session_state.current_generation.pop(idx)
                        st.warning("Rejected.")
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
        st.warning("⚠️ ClickHouse is unreachable — showing resilient in-memory fallback benchmarks.")

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

        # Crop-mode performance — starts as a single 'unknown' bucket
        # (seed data + pre-tracking rows) and fills in with real
        # center_crop vs blurred_background comparisons as you generate
        # more clips with different crop modes selected.
        crop_df = _cached_crop_benchmarks(urdr)
        if not crop_df.empty and len(crop_df) > 1:
            st.markdown("<div class='workflow-header'>✂️ Crop Mode Performance</div>", unsafe_allow_html=True)
            crop_fig = px.bar(
                crop_df, x="crop_mode", y=["avg_3s_retention", "avg_completion_rate", "avg_virality_score"],
                barmode="group", template="plotly_dark",
                labels={"value": "Score / Percent", "crop_mode": "Crop Mode", "variable": "Metric"},
            )
            st.plotly_chart(crop_fig, width='stretch')
        elif not crop_df.empty:
            st.caption(f"Crop mode data is all '{crop_df.iloc[0]['crop_mode']}' so far — generate clips with different crop modes to compare.")
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

        display_df = outcomes_df.copy()
        display_df["youtube_url"] = display_df["youtube_url"]
        st.dataframe(
            display_df[[
                "clip_id", "hook_type", "predicted_virality_score", "predicted_3s_retention_pct",
                "actual_view_count", "actual_like_count", "actual_comment_count",
                "youtube_url", "last_synced_at",
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