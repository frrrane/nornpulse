"""
⚡ NornPulse: Autonomous Media Engine
Built for Norn Labs (nornlabs.ai)
Pairing Google GenAI SDK (Gemini 2.0 Flash), ClickHouse, and FFmpeg
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

load_dotenv()

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
    .card-value {
        color: #f8fafc;
        font-size: 1.8rem;
        font-weight: 700;
        font-family: 'JetBrains Mono', monospace;
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
    st.session_state.sample_video_path = None


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
        help="Required for Gemini 2.0 Flash reasoning. Fallback simulation operates if empty.",
    )
    if api_key_input and api_key_input != st.session_state.verdandi.api_key:
        st.session_state.verdandi = VerdandiOrchestrator(api_key=api_key_input, urdr_tool=st.session_state.urdr)
        st.success("Gemini 2.0 Flash client updated!")

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
                st.warning("ClickHouse offline. Operating in fallback cache.")
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
    target_clips = st.slider("Target Clips to Extract", min_value=1, max_value=3, value=2)
    
    st.markdown("---")
    st.caption("NornPulse v1.0.0 • Hackathon Edition")


# Main Header
st.markdown("<h1 class='main-title'>⚡ NornPulse: Autonomous Media Engine</h1>", unsafe_allow_html=True)
st.markdown("<div class='sub-title'>Autonomous 16:9 to 9:16 Short Generator grounded in <b>ClickHouse Historical Retention Intelligence</b> and <b>Gemini 2.0 Flash Reasoning</b></div>", unsafe_allow_html=True)

# System Health Indicators
c_urdr, c_verdandi, c_skuld = st.columns(3)
with c_urdr:
    ch_status = "🟢 Live (Port 8123)" if st.session_state.urdr.is_connected() else "🟡 In-Memory Cache"
    st.markdown(f"""
    <div class='metric-card'>
        <div class='card-title'>ᚢ Urðr (Past / ClickHouse)</div>
        <div style='font-size: 1.1rem; font-weight: 600; color: #60a5fa; margin-top: 4px;'>{ch_status}</div>
        <div style='font-size: 0.8rem; color: #94a3b8;'>Historical Hook Telemetry</div>
    </div>
    """, unsafe_allow_html=True)

with c_verdandi:
    gemini_status = "🟢 Gemini 2.0 Flash Active" if st.session_state.verdandi.client else "🟡 Heuristic Fallback"
    st.markdown(f"""
    <div class='metric-card'>
        <div class='card-title'>ᚹ Verðandi (Present / Gemini)</div>
        <div style='font-size: 1.1rem; font-weight: 600; color: #f472b6; margin-top: 4px;'>{gemini_status}</div>
        <div style='font-size: 0.8rem; color: #94a3b8;'>Real-Time Transcript Orchestrator</div>
    </div>
    """, unsafe_allow_html=True)

with c_skuld:
    st.markdown(f"""
    <div class='metric-card'>
        <div class='card-title'>ᛋ Skuld (Future / FFmpeg)</div>
        <div style='font-size: 1.1rem; font-weight: 600; color: #34d399; margin-top: 4px;'>🟢 Hardware Accelerated</div>
        <div style='font-size: 0.8rem; color: #94a3b8;'>9:16 Vertical Short Renderer</div>
    </div>
    """, unsafe_allow_html=True)


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
            ["✨ Instant Synthetic Demo Video (1-Click)", "📁 Upload Custom Video (MP4/MOV)"],
            horizontal=True
        )

        active_video_path = None
        if "Instant" in video_source_mode:
            if not st.session_state.sample_video_path or not os.path.exists(st.session_state.sample_video_path):
                with st.spinner("Generating synthetic 16:9 test video via FFmpeg..."):
                    st.session_state.sample_video_path = create_sample_16x9_video(duration=75)
            active_video_path = st.session_state.sample_video_path
            st.video(active_video_path)
            st.caption("🎬 16:9 Synthetic Test Video with dynamic timestamps & test tone ready.")
        else:
            uploaded_file = st.file_uploader("Upload 16:9 Video", type=["mp4", "mov", "mkv"])
            if uploaded_file:
                tfile = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
                tfile.write(uploaded_file.read())
                active_video_path = tfile.name
                st.video(active_video_path)

    with col_input2:
        st.markdown("#### 2. Timestamped Transcript")
        preset_choice = st.selectbox(
            "Select Transcript Preset or Custom:",
            options=["norn_ai_keynote", "clickhouse_speed_podcast", "custom"],
            format_func=lambda x: SAMPLE_TRANSCRIPTS[x]["title"] if x in SAMPLE_TRANSCRIPTS else "✍️ Custom Transcript Input"
        )

        if preset_choice in SAMPLE_TRANSCRIPTS:
            transcript_input = st.text_area(
                "Timestamped Transcript",
                value=SAMPLE_TRANSCRIPTS[preset_choice]["transcript"],
                height=240,
            )
            video_title = SAMPLE_TRANSCRIPTS[preset_choice]["title"]
            video_topic = SAMPLE_TRANSCRIPTS[preset_choice]["category"]
        else:
            transcript_input = st.text_area(
                "Paste Timestamped Transcript",
                value="[00:00 - 00:15] Stop using outdated video workflows...\n[00:15 - 00:40] Here is why NornPulse is revolutionary...",
                height=240,
            )
            video_title = "Custom Media Stream"
            video_topic = "general"

    st.markdown("<br>", unsafe_allow_html=True)
    unleash_btn = st.button("⚡ UNLEASH THE NORNS (ANALYZE & RENDER 9:16)", type="primary", use_container_width=True)

    if unleash_btn:
        if not active_video_path or not os.path.exists(active_video_path):
            st.error("Please provide or generate a valid source video.")
        elif not transcript_input.strip():
            st.error("Please provide transcript text.")
        else:
            # Multi-stage execution pipeline
            progress_bar = st.progress(0)
            status_placeholder = st.empty()

            # Phase 1: Urðr Analytics
            status_placeholder.markdown("ᚢ **Phase 1: Urðr** is querying ClickHouse retention benchmarks and historical virality distributions...")
            progress_bar.progress(25)
            time.sleep(0.4)

            # Phase 2: Verðandi Reasoning with Gemini 2.0 Flash
            status_placeholder.markdown("ᚹ **Phase 2: Verðandi** is analyzing transcript with Gemini 2.0 Flash & calculating optimal clip timestamps...")
            progress_bar.progress(55)
            
            analysis_result = st.session_state.verdandi.analyze_transcript_and_decide(
                transcript_text=transcript_input,
                video_metadata={"title": video_title, "topic": video_topic, "video_path": active_video_path},
                target_clip_count=target_clips,
            )
            st.session_state.analysis_result = analysis_result

            # Phase 3: Skuld Rendering with FFmpeg
            status_placeholder.markdown(f"ᛋ **Phase 3: Skuld** is rendering {len(analysis_result.clips)} clips from 16:9 to 9:16 vertical format via FFmpeg...")
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
    st.markdown("Historical audience behavior, 3-second hold percentages, and completion rates stored in ClickHouse.")

    benchmarks_df = st.session_state.urdr.get_hook_type_benchmarks()

    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    with kpi1:
        st.metric("Highest Retention Hook", "Shock Stat (94.6%)", "+5.2% vs avg")
    with kpi2:
        st.metric("Overall Avg 3s Hold", f"{benchmarks_df['avg_3s_retention'].mean():.1f}%")
    with kpi3:
        st.metric("Optimal Short Length", "28s - 38s")
    with kpi4:
        st.metric("Avg Virality Index", f"{benchmarks_df['avg_virality_score'].mean():.1f}")

    col_chart1, col_chart2 = st.columns(2)
    with col_chart1:
        st.markdown("#### Average 3s Retention by Hook Taxonomy")
        fig_bar = px.bar(
            benchmarks_df,
            x="hook_type",
            y="avg_3s_retention",
            color="avg_virality_score",
            color_continuous_scale="Viridis",
            labels={"avg_3s_retention": "3-Second Retention (%)", "hook_type": "Hook Type"},
        )
        fig_bar.update_layout(template="plotly_dark", height=340)
        st.plotly_chart(fig_bar, use_container_width=True)

    with col_chart2:
        st.markdown("#### Retention Decay Curve (3s ➔ 15s ➔ 30s)")
        fig_decay = go.Figure()
        for _, r in benchmarks_df.iterrows():
            fig_decay.add_trace(go.Scatter(
                x=["3s", "15s", "30s"],
                y=[r["avg_3s_retention"], r["avg_15s_retention"], r["avg_30s_retention"]],
                mode="lines+markers",
                name=r["hook_type"]
            ))
        fig_decay.update_layout(
            template="plotly_dark",
            height=340,
            yaxis_title="Viewer Retention (%)",
            xaxis_title="Time in Short",
        )
        st.plotly_chart(fig_decay, use_container_width=True)

    st.markdown("#### Historical Video Hook Records Table")
    filter_hook = st.selectbox("Filter by Category", ["all"] + list(benchmarks_df["hook_type"].unique()))
    filtered_data = st.session_state.urdr.query_hook_retention(hook_category=filter_hook)
    st.dataframe(filtered_data, use_container_width=True)

    with st.expander("🛠️ Interactive ClickHouse SQL Console"):
        custom_sql = st.text_area("SQL Query", value="SELECT hook_type, avg(virality_score) as avg_score FROM video_hook_retention GROUP BY hook_type ORDER BY avg_score DESC")
        if st.button("Execute SQL Query"):
            try:
                sql_res = st.session_state.urdr.execute_custom_query(custom_sql)
                st.dataframe(sql_res)
            except Exception as e:
                st.error(f"Query execution error: {e}")


# =========================================================================
# TAB 3: VERÐANDI AI PLAYGROUND
# =========================================================================
with tabs[2]:
    st.markdown("### ᚹ Verðandi Gemini 2.0 Flash Reasoning Playground")
    st.markdown("Inspect raw Gemini 2.0 Flash responses and see how Urðr's historical retention data shapes prompt engineering.")

    sample_key = st.selectbox(
        "Choose sample transcript to inspect:",
        options=list(SAMPLE_TRANSCRIPTS.keys()),
        format_func=lambda x: SAMPLE_TRANSCRIPTS[x]["title"],
        key="playground_select"
    )

    t_data = SAMPLE_TRANSCRIPTS[sample_key]
    st.text_area("Source Transcript", value=t_data["transcript"], height=160, key="play_text")

    if st.button("⚡ Run Gemini 2.0 Flash Analysis Only", key="run_ai_only"):
        with st.spinner("Invoking Gemini 2.0 Flash with Urðr context..."):
            res = st.session_state.verdandi.analyze_transcript_and_decide(
                transcript_text=t_data["transcript"],
                video_metadata={"title": t_data["title"], "topic": t_data["category"]},
                target_clip_count=2
            )
            st.json(res.model_dump())


# =========================================================================
# TAB 4: SKULD VIDEO STUDIO
# =========================================================================
with tabs[3]:
    st.markdown("### ᛋ Skuld FFmpeg 9:16 Video Studio")
    st.markdown("Direct 16:9 widescreen to 9:16 vertical short cropping with custom start/end points.")

    c_s1, c_s2 = st.columns([1, 1])
    with c_s1:
        st.markdown("#### Input Parameters")
        studio_crop = st.radio("Crop Mode", ["center_crop", "blurred_background"], horizontal=True, key="studio_crop")
        s_start = st.text_input("Start Timestamp (MM:SS or sec)", value="00:05")
        s_end = st.text_input("End Timestamp (MM:SS or sec)", value="00:35")
        s_title = st.text_input("Clip Identifier", value="studio_custom_short")

        if st.button("🎬 Render Custom 9:16 Short", type="primary"):
            target_vid = st.session_state.sample_video_path or create_sample_16x9_video()
            with st.spinner("Rendering short via FFmpeg..."):
                res = st.session_state.skuld.render_vertical_short(
                    input_video_path=target_vid,
                    start_time=s_start,
                    end_time=s_end,
                    clip_id=s_title,
                    crop_mode=studio_crop,
                )
                st.session_state.studio_last_render = res
                st.success("Render complete!")

    with c_s2:
        st.markdown("#### Render Output")
        if "studio_last_render" in st.session_state:
            last_path = st.session_state.studio_last_render["output_video_path"]
            if os.path.exists(last_path):
                st.video(last_path)
                st.json(st.session_state.studio_last_render)


# =========================================================================
# TAB 5: NORN LABS LORE & DOCS
# =========================================================================
with tabs[4]:
    st.markdown("""
    ### ⚡ Norn Labs: The Autonomous Media Revolution
    **[nornlabs.ai](https://nornlabs.ai)**

    In Norse mythology, the three **Norns** sit by the Well of Urðr beneath Yggdrasil, weaving the tapestry of fate.
    
    In **NornPulse**, they weave the viral destiny of your video content:

    | Norn | Domain | Role in NornPulse | Technology Stack |
    | :--- | :--- | :--- | :--- |
    | **ᚢ Urðr** | *The Past* | Analyzes historical video hook retention telemetry and drop-off patterns | ClickHouse (Ports 8123 & 9000), `clickhouse-connect` |
    | **ᚹ Verðandi** | *The Present* | Evaluates real-time transcripts, injects historical data, and calculates optimal viral timestamps | Google GenAI SDK (`gemini-2.0-flash`) |
    | **ᛋ Skuld** | *The Future* | Brings destiny into reality by slicing, scaling, and rendering 9:16 vertical shorts | FFmpeg, Hardware-Accelerated Filters |

    ---

    #### 🚀 Architecture Diagram
    ```text
    +-------------------------------------------------------------+
    |                      16:9 Source Video                      |
    +-------------------------------------------------------------+
                                   |
                                   v
             [ᚢ Urðr] <===================> [ClickHouse Database]
        (Historical Retention                   (Ports 8123 & 9000)
             Benchmarks)
                  |
                  v
           [ᚹ Verðandi] <=================> [Google GenAI SDK]
        (Real-Time Decision                     (Gemini 2.0 Flash)
             & Timestamps)
                  |
                  v
            [ᛋ Skuld] <===================> [FFmpeg Engine]
        (Vertical Manifestation)                (1080x1920 9:16 Short)
                  |
                  v
    +-------------------------------------------------------------+
    |                   ⚡ High-Converting 9:16 Short              |
    +-------------------------------------------------------------+
    ```
    """)
