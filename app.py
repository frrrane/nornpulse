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
from utils.db_logger import log_render_event, init_telemetry_table

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


# Helper function to fetch YouTube transcripts automatically
def fetch_youtube_transcript_text(url: str) -> str:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        import re
        
        # Extract video ID
        vid_match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", url)
        if not vid_match:
            return "[00:00 - 00:05] Autonomous video analysis stream initialization."
        
        vid_id = vid_match.group(1)
        transcript_list = YouTubeTranscriptApi.get_transcript(vid_id)
        
        formatted_lines = []
        for entry in transcript_list:
            start = entry['start']
            dur = entry.get('duration', 5.0)
            end = start + dur
            text = entry['text'].replace('\n', ' ')
            
            m_s = int(start // 60)
            s_s = int(start % 60)
            m_e = int(end // 60)
            s_e = int(end % 60)
            
            formatted_lines.append(f"[{m_s:02d}:{s_s:02d} - {m_e:02d}:{s_e:02d}] {text}")
            
        return "\n".join(formatted_lines)
    except Exception as e:
        return f"[00:00 - 00:10] Automatic transcript extraction fallback active ({e})."


# Sidebar Configuration
with st.sidebar:
    st.markdown("### ⚡ NORN LABS")
    st.markdown("**[nornlabs.ai](https://nornlabs.ai)** • *Autonomous Media*")
    st.divider()

    st.markdown("#### 🔑 Model & Intelligence")
    api_key_input = st.text_input(
        "Google Gemini API Key",
        value=os.getenv("GEMINI_API_KEY", ""),
        type="password",
        help="Required for Gemini 3.6 Flash reasoning.",
    )
    if api_key_input and api_key_input != st.session_state.verdandi.api_key:
        st.session_state.verdandi = VerdandiOrchestrator(api_key=api_key_input, urdr_tool=st.session_state.urdr)
        st.success("Gemini 3.6 Flash client updated!")

    st.markdown("#### 🗄️ ClickHouse Engine")
    ch_host = st.text_input("Host", value=os.getenv("CLICKHOUSE_HOST", "localhost"))
    ch_port = st.number_input("Port (HTTP)", value=int(os.getenv("CLICKHOUSE_PORT", "8123")), step=1)
    
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
st.markdown("<div class='sub-title'>Autonomous 16:9 to 9:16 Short Generator grounded in <b>ClickHouse Historical Retention Intelligence</b> and <b>Gemini 3.6 Flash Reasoning</b></div>", unsafe_allow_html=True)


# Tabs Navigation
tabs = st.tabs([
    "🚀 Autonomous Pipeline",
    "ᚢ Urðr Analytics Hub",
    "ᚹ Verðandi AI Playground",
    "ᛋ Skuld Video Studio",
    "📜 Norn Labs Lore & Docs"
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
            ["🌐 YouTube URL Link", "✨ Instant Synthetic Demo Video", "📁 Upload Custom Video"],
            horizontal=True
        )

        active_video_path = None
        if "YouTube" in video_source_mode:
            # Hardcoded Big Buck Bunny URL default
            default_yt_url = "https://www.youtube.com/watch?v=2GgV7bgBS4Q"
            yt_url_input = st.text_input("YouTube Video URL:", value=default_yt_url)
            
            # Check if cached video file already exists
            target_cache_path = Path("sample_data/yt_input.mp4")
            if not target_cache_path.exists() and yt_url_input:
                with st.spinner("Downloading YouTube video stream via yt-dlp (cached for future runs)..."):
                    try:
                        active_video_path = download_youtube_video(yt_url_input)
                        st.session_state.sample_video_path = active_video_path
                    except Exception as e:
                        st.error(f"Download failed: {e}")
            else:
                active_video_path = str(target_cache_path)
                st.session_state.sample_video_path = active_video_path

            if os.path.exists(active_video_path):
                st.video(active_video_path)
                st.caption("🎬 Cached Big Buck Bunny stream ready.")

        elif "Instant" in video_source_mode:
            if not st.session_state.sample_video_path or not os.path.exists(st.session_state.sample_video_path):
                with st.spinner("Generating synthetic 16:9 test video via FFmpeg..."):
                    st.session_state.sample_video_path = create_sample_16x9_video(duration=75)
            active_video_path = st.session_state.sample_video_path
            st.video(active_video_path)
        else:
            uploaded_file = st.file_uploader("Upload 16:9 Video", type=["mp4", "mov", "mkv"])
            if uploaded_file:
                tfile = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
                tfile.write(uploaded_file.read())
                active_video_path = tfile.name
                st.video(active_video_path)

    with col_input2:
        st.markdown("#### 2. Timestamped Transcript (Auto-Fetched)")
        
        # Automatically pull transcript if YouTube URL is used
        auto_transcript = ""
        if "YouTube" in video_source_mode and 'yt_url_input' in locals():
            auto_transcript = fetch_youtube_transcript_text(yt_url_input)
        
        transcript_input = st.text_area(
            "Timestamped Transcript",
            value=auto_transcript if auto_transcript else SAMPLE_TRANSCRIPTS["norn_ai_keynote"]["transcript"],
            height=240,
        )
        video_title = "Big Buck Bunny Automated Stream"
        video_topic = "animation"

    st.markdown("<br>", unsafe_allow_html=True)
    unleash_btn = st.button("⚡ UNLEASH THE NORNS (ANALYZE & RENDER 9:16)", type="primary", use_container_width=True)

    if unleash_btn:
        if not active_video_path or not os.path.exists(active_video_path):
            st.error("Please provide or generate a valid source video.")
        elif not transcript_input.strip():
            st.error("Please provide transcript text.")
        else:
            progress_bar = st.progress(0)
            status_placeholder = st.empty()

            # Phase 1: Urðr Analytics
            status_placeholder.markdown("ᚢ **Phase 1: Urðr** is querying ClickHouse retention benchmarks...")
            progress_bar.progress(25)
            time.sleep(0.3)

            # Phase 2: Verðandi Reasoning with Gemini 3.6 Flash
            status_placeholder.markdown("ᚹ **Phase 2: Verðandi** is analyzing transcript with Gemini 3.6 Flash...")
            progress_bar.progress(55)
            
            analysis_result = st.session_state.verdandi.analyze_transcript_and_decide(
                transcript_text=transcript_input,
                video_metadata={"title": video_title, "topic": video_topic, "video_path": active_video_path},
                target_clip_count=target_clips,
            )
            st.session_state.analysis_result = analysis_result

            # Phase 3: Skuld Rendering with FFmpeg
            status_placeholder.markdown(f"ᛋ **Phase 3: Skuld** is rendering {len(analysis_result.clips)} clips via FFmpeg...")
            progress_bar.progress(80)

            rendered_clips = []
            for idx, clip in enumerate(analysis_result.clips):
                status_placeholder.markdown(f"ᛋ **Skuld** rendering `{clip.clip_id}` ({clip.start_time} ➔ {clip.end_time})...")
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
                        stage="Skuld_Compiler"
                    )
                except Exception:
                    pass

            st.session_state.rendered_clips = rendered_clips
            progress_bar.progress(100)
            status_placeholder.success(f"✨ Destiny Fulfilled! Successfully generated {len(rendered_clips)} vertical short(s).")

    # Display Generated Clips
    if st.session_state.rendered_clips:
        st.divider()
        st.markdown("### 🏆 Rendered Vertical Shorts (9:16)")

        for idx, item in enumerate(st.session_state.rendered_clips):
            clip = item["decision"]
            render = item["render"]

            with st.container():
                st.markdown(f"#### Short #{idx+1}: {clip.hook_title}")
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
                                use_container_width=True
                            )
                    else:
                        st.error(f"Render output missing: {out_path}")

                with c_info:
                    st.markdown(f"""
                    <span class='norn-badge badge-urdr'>Hook: {clip.hook_type}</span>
                    <span class='norn-badge badge-verdandi'>Virality: {clip.virality_score}/100</span>
                    <span class='norn-badge badge-skuld'>Duration: {clip.duration_seconds}s</span>
                    """, unsafe_allow_html=True)

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
            target_clip_count=1
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
            start_time="00:02", end_time="00:07", clip_id="studio_custom"
        )
        st.video(res["output_video_path"])


# =========================================================================
# TAB 5: NORN LABS LORE & DOCS
# =========================================================================
with tabs[4]:
    st.markdown("### ⚡ Norn Labs: The Autonomous Media Revolution")
    st.markdown("Automated 16:9 to 9:16 vertical shorts powered by Urðr, Verðandi, and Skuld.")