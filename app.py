"""
⚡ NornPulse: Autonomous Media Engine
Built for Norn Labs (nornlabs.ai)
Pairing Google GenAI SDK (Gemini 3.6 Flash), ClickHouse, and FFmpeg
"""

import os
import time
import tempfile
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from pathlib import Path
from dotenv import load_dotenv

# Import Norn Agent Triad
from agent.urdr_analytics import UrdrAnalytics
from agent.verdandi_orchestrator import VerdandiOrchestrator
from agent.skuld_renderer import SkuldRenderer
from utils.sample_generator import SAMPLE_TRANSCRIPTS, create_sample_16x9_video
from utils.ingest import download_youtube_video
from utils.transcribe import get_or_create_transcript

# Central environment-driven configuration
from config import Config

# Optional telemetry logger (safe if missing)
try:
    from utils.db_logger import log_render_event, init_telemetry_table
except ImportError:
    def log_render_event(*args, **kwargs): pass
    def init_telemetry_table(): pass

load_dotenv()


# Initialize ClickHouse telemetry table on startup
try:
    init_telemetry_table()
except Exception:
    pass

# Streamlit Page Configuration
st.set_page_config(
    page_title="NornPulse: Autonomous Media Engine",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom Cyber-Nordic CSS
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@700&family=JetBrains+Mono:wght@400;700&family=Inter:wght@300;400;600;700&display=swap');
    
    .main-title {
        font-family: 'Cinzel', serif;
        font-size: 2.6rem;
        font-weight: 700;
        letter-spacing: 1px;
        background: linear-gradient(90deg, #00f2fe 0%, #4facfe 50%, #f093fb 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0px;
    }
    .sub-title {
        font-family: 'Inter', sans-serif;
        color: #94a3b8;
        font-size: 1.05rem;
        margin-top: -5px;
        margin-bottom: 25px;
    }
    .norn-badge {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 6px;
        font-size: 0.85rem;
        font-weight: 600;
        font-family: 'JetBrains Mono', monospace;
        margin-right: 8px;
    }
    .badge-urdr {
        background-color: rgba(59, 130, 246, 0.15);
        color: #60a5fa;
        border: 1px solid #3b82f6;
    }
    .badge-verdandi {
        background-color: rgba(236, 72, 153, 0.15);
        color: #f472b6;
        border: 1px solid #ec4899;
    }
    .badge-skuld {
        background-color: rgba(16, 185, 129, 0.15);
        color: #34d399;
        border: 1px solid #10b981;
    }
    .metric-card {
        background: rgba(15, 23, 42, 0.6);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
        padding: 16px;
        margin-bottom: 12px;
    }
    .card-title {
        color: #94a3b8;
        font-size: 0.85rem;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
</style>
""", unsafe_allow_html=True)


# Initialize Session State
if "urdr" not in st.session_state:
    st.session_state.urdr = UrdrAnalytics()
if "skuld" not in st.session_state:
    st.session_state.skuld = SkuldRenderer()
if "verdandi" not in st.session_state:
    st.session_state.verdandi = VerdandiOrchestrator(urdr_tool=st.session_state.urdr)
if "analysis_result" not in st.session_state:
    st.session_state.analysis_result = None
if "rendered_clips" not in st.session_state:
    st.session_state.rendered_clips = []
if "sample_video_path" not in st.session_state:
    st.session_state.sample_video_path = "sample_data/yt_input.mp4"


# Helper: YouTube captions fallback (optional)
def fetch_youtube_transcript_text(url: str) -> str:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        import re
        vid_match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", url)
        if not vid_match:
            return ""
        vid_id = vid_match.group(1)
        transcript_list = YouTubeTranscriptApi.get_transcript(vid_id)
        formatted_lines = []
        for entry in transcript_list:
            start = entry["start"]
            dur = entry.get("duration", 5.0)
            end = start + dur
            text = entry["text"].replace("\n", " ")
            m_s, s_s = int(start // 60), int(start % 60)
            m_e, s_e = int(end // 60), int(end % 60)
            formatted_lines.append(f"[{m_s:02d}:{s_s:02d} - {m_e:02d}:{s_e:02d}] {text}")
        return "\n".join(formatted_lines)
    except Exception:
        return ""


# Sidebar Configuration
with st.sidebar:
    st.markdown("### ⚡ NORN LABS")
    st.markdown("**[nornlabs.ai](https://nornlabs.ai)** • *Autonomous Media*")
    st.divider()

    st.markdown("#### 🔑 Model & Intelligence")
    api_key_input = st.text_input(
        "Google Gemini API Key",
        value=Config.GEMINI_API_KEY,
        type="password",
        help="Required for Gemini 3.6 Flash reasoning.",
    )
    if api_key_input and api_key_input != st.session_state.verdandi.api_key:
        st.session_state.verdandi = VerdandiOrchestrator(
            api_key=api_key_input, urdr_tool=st.session_state.urdr
        )
        st.success("Gemini 3.6 Flash client updated!")

    st.markdown("#### 🗄️ ClickHouse Engine")
    ch_host = st.text_input("Host", value=Config.CLICKHOUSE_HOST)
    ch_port = st.number_input("Port (HTTP)", value=Config.CLICKHOUSE_PORT, step=1)


    col_c1, col_c2 = st.columns(2)
    with col_c1:
        if st.button("Reconnect", use_container_width=True):
            st.session_state.urdr = UrdrAnalytics(host=ch_host, port=ch_port)
            st.session_state.verdandi.urdr = st.session_state.urdr
            if st.session_state.urdr.is_connected():
                st.success("Connected to ClickHouse!")
            else:
                st.warning("ClickHouse offline.")
    with col_c2:
        if st.button("Seed DB", use_container_width=True):
            count = st.session_state.urdr.seed_benchmarks()
            st.info(f"Seeded {count} rows")

    st.divider()
    st.markdown("#### 🎬 Render Settings (Skuld)")
    crop_mode = st.selectbox(
        "Aspect Ratio Crop Mode",
        options=["center_crop", "blurred_background"],
        format_func=lambda x: "🎯 High-Res Center Crop (9:16)" if x == "center_crop" else "✨ Blurred Background Canvas (9:16)",
    )
    target_clips = st.slider("Target Clips to Extract", min_value=1, max_value=3, value=1)


# Main Header
st.markdown("<h1 class='main-title'>⚡ NornPulse: Autonomous Media Engine</h1>", unsafe_allow_html=True)
st.markdown(
    "<div class='sub-title'>Autonomous 16:9 to 9:16 Short Generator grounded in "
    "<b>ClickHouse Historical Retention Intelligence</b> and <b>Gemini 3.6 Flash Reasoning</b></div>",
    unsafe_allow_html=True,
)


# Tabs Navigation  ← THIS LINE IS REQUIRED
tabs = st.tabs([
    "🚀 Autonomous Pipeline",
    "ᚢ Urðr Analytics Hub",
    "ᚹ Verðandi AI Playground",
    "ᛋ Skuld Video Studio",
    "📜 Norn Labs Lore & Docs",
])


# =========================================================================
# TAB 1: AUTONOMOUS PIPELINE
# =========================================================================
with tabs[0]:
    st.markdown("### 🔮 Autonomous Video Pipeline")
    st.markdown("Transform long-form 16:9 video content into viral 9:16 vertical shorts powered by the three Norns.")

    col_input1, col_input2 = st.columns([1, 1])

    with col_input1:
        st.markdown("#### 1. Source Video (16:9)")
        video_source_mode = st.radio(
            "Video Input Source:",
            [
                "🌍 Carl Sagan (auto transcript)",
                "🌐 YouTube URL Link",
                "✨ Instant Synthetic Demo Video",
                "📁 Upload Custom Video",
            ],
            horizontal=False,
        )

        active_video_path = None
        transcript_input = ""
        video_title = "Custom Media Stream"
        video_topic = "general"

        if "Carl Sagan" in video_source_mode:
            sagan_path = Path("sample_data/yt_input.mp4")
            if not sagan_path.exists():
                st.error("Sagan video not found. Download it first:")
                st.code(
                    "python -c \"from utils.ingest import download_youtube_video; "
                    "download_youtube_video('https://www.youtube.com/watch?v=tLPkpBN6bEI')\""
                )
            else:
                active_video_path = str(sagan_path)
                st.session_state.sample_video_path = active_video_path
                st.video(active_video_path)
                st.caption("🌍 Carl Sagan – We Are Their Children (Cosmos)")

                with st.spinner("Generating / loading transcript with Whisper (tiny)..."):
                    try:
                        transcript_input = get_or_create_transcript(
                            sagan_path, model_size="tiny"
                        )
                        st.success("Transcript ready")
                    except Exception as e:
                        st.error(f"Transcription failed: {e}")
                        transcript_input = ""

                video_title = "Carl Sagan – We Are Their Children"
                video_topic = "science_cosmos"

        elif "YouTube" in video_source_mode:
            default_yt_url = Config.INPUT_VIDEO_SOURCE or "https://www.youtube.com/watch?v=tLPkpBN6bEI"
            yt_url_input = st.text_input("YouTube Video URL:", value=default_yt_url)

            if yt_url_input:
                with st.spinner("Downloading YouTube video via yt-dlp..."):
                    try:
                        active_video_path = download_youtube_video(yt_url_input)
                        st.session_state.sample_video_path = active_video_path
                    except Exception as e:
                        st.error(f"Download failed: {e}")
                        active_video_path = None

            if active_video_path and os.path.exists(active_video_path):
                st.video(active_video_path)
                st.caption("🎬 Downloaded YouTube stream ready.")

                with st.spinner("Generating transcript..."):
                    try:
                        transcript_input = get_or_create_transcript(
                            active_video_path, model_size="tiny"
                        )
                    except Exception:
                        transcript_input = fetch_youtube_transcript_text(yt_url_input)

                video_title = "YouTube Source"
                video_topic = "general"

        elif "Instant" in video_source_mode:
            if (
                not st.session_state.sample_video_path
                or not os.path.exists(st.session_state.sample_video_path)
                or "yt_input" in str(st.session_state.sample_video_path)
            ):
                with st.spinner("Generating synthetic 16:9 test video via FFmpeg..."):
                    st.session_state.sample_video_path = create_sample_16x9_video(duration=75)
            active_video_path = st.session_state.sample_video_path
            st.video(active_video_path)
            st.caption("🎬 Synthetic test video")

            transcript_input = SAMPLE_TRANSCRIPTS.get("norn_ai_keynote", {}).get("transcript", "")
            video_title = SAMPLE_TRANSCRIPTS.get("norn_ai_keynote", {}).get("title", "Synthetic Demo")
            video_topic = "tech_ai"

        else:  # Upload
            uploaded_file = st.file_uploader("Upload 16:9 Video", type=["mp4", "mov", "mkv"])
            if uploaded_file:
                tfile = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
                tfile.write(uploaded_file.read())
                active_video_path = tfile.name
                st.video(active_video_path)

                with st.spinner("Transcribing uploaded video..."):
                    try:
                        transcript_input = get_or_create_transcript(
                            active_video_path, model_size="tiny"
                        )
                    except Exception as e:
                        st.warning(f"Auto-transcript failed: {e}. You can paste one manually.")
                        transcript_input = ""

    with col_input2:
        st.markdown("#### 2. Timestamped Transcript")
        transcript_input = st.text_area(
            "Transcript (auto-generated – you can edit)",
            value=transcript_input,
            height=280,
        )

    st.markdown("<br>", unsafe_allow_html=True)
    unleash_btn = st.button(
        "⚡ UNLEASH THE NORNS (ANALYZE & RENDER 9:16)",
        type="primary",
        use_container_width=True,
    )

    if unleash_btn:
        if not active_video_path or not os.path.exists(active_video_path):
            st.error("Please provide or generate a valid source video.")
        elif not transcript_input.strip():
            st.error("Please provide transcript text.")
        else:
            progress_bar = st.progress(0)
            status_placeholder = st.empty()

            # Phase 1
            status_placeholder.markdown("ᚢ **Phase 1: Urðr** is querying ClickHouse retention benchmarks...")
            progress_bar.progress(25)
            time.sleep(0.3)

            # Phase 2
            status_placeholder.markdown("ᚹ **Phase 2: Verðandi** is analyzing transcript with Gemini 3.6 Flash...")
            progress_bar.progress(55)

            analysis_result = st.session_state.verdandi.analyze_transcript_and_decide(
                transcript_text=transcript_input,
                video_metadata={
                    "title": video_title,
                    "topic": video_topic,
                    "video_path": active_video_path,
                },
                target_clip_count=target_clips,
            )
            st.session_state.analysis_result = analysis_result

            # Phase 3
            status_placeholder.markdown(
                f"ᛋ **Phase 3: Skuld** is rendering {len(analysis_result.clips)} clips via FFmpeg..."
            )
            progress_bar.progress(80)

            rendered_clips = []
            for idx, clip in enumerate(analysis_result.clips):
                status_placeholder.markdown(
                    f"ᛋ **Skuld** rendering `{clip.clip_id}` ({clip.start_time} ➔ {clip.end_time})..."
                )
                render_res = st.session_state.skuld.render_vertical_short(
                    input_video_path=active_video_path,
                    start_time=clip.start_time,
                    end_time=clip.end_time,
                    clip_id=clip.clip_id,
                    crop_mode=crop_mode,
                    hook_banner_text=clip.hook_title,
                )
                rendered_clips.append({"decision": clip, "render": render_res})

                try:
                    log_render_event(
                        video_name=Path(active_video_path).name,
                        duration=float(clip.duration_seconds),
                        status="SUCCESS",
                        stage="Skuld_Compiler",
                    )
                except Exception:
                    pass

            st.session_state.rendered_clips = rendered_clips
            progress_bar.progress(100)
            status_placeholder.success(
                f"✨ Destiny Fulfilled! Successfully generated {len(rendered_clips)} vertical short(s)."
            )

    # Display Generated Clips
    if st.session_state.rendered_clips:
        st.divider()
        st.markdown("### 🏆 Rendered Vertical Shorts (9:16)")

        for idx, item in enumerate(st.session_state.rendered_clips):
            clip = item["decision"]
            render = item["render"]

            with st.container():
                st.markdown(f"#### Short #{idx + 1}: {clip.hook_title}")
                c_vid, c_info = st.columns([1, 1.2])

                with c_vid:
                    out_path = render["output_video_path"]
                    if os.path.exists(out_path):
                        st.video(out_path)
                        with open(out_path, "rb") as f:
                            st.download_button(
                                label=f"⬇️ Download Short ({clip.clip_id}.mp4)",
                                data=f.read(),
                                file_name=f"{clip.clip_id}_9x16.mp4",
                                mime="video/mp4",
                                key=f"dl_{clip.clip_id}_{idx}",
                                use_container_width=True,
                            )
                    else:
                        st.error(f"Render output missing: {out_path}")

                with c_info:
                    st.markdown(
                        f"""
                    <span class='norn-badge badge-urdr'>Hook: {clip.hook_type}</span>
                    <span class='norn-badge badge-verdandi'>Virality: {clip.virality_score}/100</span>
                    <span class='norn-badge badge-skuld'>Duration: {clip.duration_seconds}s</span>
                    """,
                        unsafe_allow_html=True,
                    )

                    m1, m2 = st.columns(2)
                    with m1:
                        st.metric("Predicted 3s Retention", f"{clip.predicted_3s_retention}%")
                    with m2:
                        st.metric("Predicted Completion", f"{clip.predicted_completion_rate}%")

                    st.markdown(f"**⚡ Rationale:** {clip.urgency_rationale}")
                    st.markdown(f"**🎯 Crop Strategy:** {clip.recommended_crop_focus}")
                    st.markdown(f"**📱 Social Copy:** *{clip.social_caption}*")
                    st.markdown(f"**🏷️ Hashtags:** `{' '.join(clip.hashtags)}`")
                st.divider()


# =========================================================================
# TAB 2: URÐR ANALYTICS HUB
# =========================================================================
with tabs[1]:
    st.markdown("### ᚢ Urðr ClickHouse Retention Intelligence")
    benchmarks_df = st.session_state.urdr.get_hook_type_benchmarks()
    st.dataframe(benchmarks_df, use_container_width=True)


# =========================================================================
# TAB 3: VERÐANDI AI PLAYGROUND
# =========================================================================
with tabs[2]:
    st.markdown("### ᚹ Verðandi Gemini 3.6 Flash Reasoning Playground")
    if st.button("⚡ Run Gemini Analysis Only", key="run_ai_only"):
        res = st.session_state.verdandi.analyze_transcript_and_decide(
            transcript_text=SAMPLE_TRANSCRIPTS["norn_ai_keynote"]["transcript"],
            video_metadata={"title": "Test Stream", "topic": "AI"},
            target_clip_count=1,
        )
        st.json(res.model_dump())


# =========================================================================
# TAB 4: SKULD VIDEO STUDIO
# =========================================================================
with tabs[3]:
    st.markdown("### ᛋ Skuld FFmpeg 9:16 Video Studio")
    if st.button("🎬 Render Custom 9:16 Short", type="primary"):
        res = st.session_state.skuld.render_vertical_short(
            input_video_path=st.session_state.sample_video_path,
            start_time="00:02",
            end_time="00:07",
            clip_id="studio_custom",
        )
        st.video(res["output_video_path"])


# =========================================================================
# TAB 5: NORN LABS LORE & DOCS
# =========================================================================
with tabs[4]:
    st.markdown("### ⚡ Norn Labs: The Autonomous Media Revolution")
    st.markdown("Automated 16:9 to 9:16 vertical shorts powered by Urðr, Verðandi, and Skuld.")