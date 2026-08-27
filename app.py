# app.py
"""
⚡ NornPulse: Autonomous Short-Form Engine (ADK Native & Multimodal)
Built for Norn Labs (nornlabs.ai)
"""

import os
import hashlib
import dataclasses
import json
import logging
import random
import re
import pandas as pd
import plotly.express as px
import streamlit as st
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

from agent.verdandi_orchestrator import (
    VerdandiOrchestrator, filter_transcript_by_window, AUTO_WINDOW_MAX_SEC, BATCH_MAX_VIDEOS,
)
from agent.skuld_renderer import (
    get_video_duration_seconds, format_seconds_to_mmss,
    CAPTION_FONTS as SkuldCaptionFonts,
)
from agent.norn_publisher import NornPublisher, PublishError
from agent import review_queue as rq
from agent import global_benchmarks as gb
from agent import calibration as cal
from agent import channels as chans
from agent import provenance as pv
from agent import scoreboard as sb
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
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    /* ── NornPulse design system ───────────────────────────────────────────
       The Norns weave fate at the well of Urðr; this app claims to do the
       same from telemetry. The palette is that well — deep water, bone,
       and worked bronze — rather than the near-black-plus-one-bright-accent
       that every other analytics dashboard lands on. Copper is reserved for
       things a human does; mint is reserved for things that were measured.
       Nothing else is allowed to be a colour.
       ------------------------------------------------------------------- */
    @import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700;12..96,800&family=Public+Sans:ital,wght@0,400;0,600;1,400&family=IBM+Plex+Mono:wght@400;600&display=swap');

    :root {
        --well:      #0B2426;   /* ground */
        --well-2:    #103133;   /* raised surface */
        --well-3:    #17403F;   /* hairline */
        --bone:      #EDE6D8;   /* primary text */
        --bone-dim:  #93A8A6;   /* secondary text */
        --copper:    #C8703C;   /* human action */
        --copper-lo: #8F4E29;
        --thread:    #6FD3C0;   /* measured value */
        --warn:      #D9A441;
        --error:     #D1503F;   /* something is actually broken */

        --display: 'Bricolage Grotesque', system-ui, sans-serif;
        --body:    'Public Sans', system-ui, sans-serif;
        --data:    'IBM Plex Mono', ui-monospace, monospace;
    }

    .stApp { background: var(--well); }

    /* layout="wide" is right for the charts on Intelligence and wrong for
       everything else: a full-width paragraph on a 27" display runs past
       any comfortable measure, and the vertical Create form would stretch
       its inputs the whole way across. A cap keeps prose readable while
       still leaving charts room. */
    .block-container { max-width: 1180px; padding-top: 2.2rem; }
    html, body, [class*="css"] {
        font-family: var(--body); color: var(--bone); font-size: 0.97rem;
    }

    /* Display face used with restraint: page titles and section marks only. */
    h1, h2, h3 {
        font-family: var(--display) !important;
        font-weight: 800 !important;
        letter-spacing: -0.018em;
        color: var(--bone);
    }

    /* Every number in this app is evidence, so numbers get the mono face. */
    [data-testid="stMetricValue"] {
        font-family: var(--data) !important;
        font-weight: 600 !important;
        color: var(--bone) !important;
        font-size: 1.55rem !important;
    }
    [data-testid="stMetricLabel"] {
        font-family: var(--body) !important;
        text-transform: uppercase; letter-spacing: 0.09em;
        font-size: 0.68rem !important; color: var(--bone-dim) !important;
    }

    /* Section mark: a rule that carries the eyebrow, so hierarchy is
       structural rather than just a larger font size. */
    .workflow-header {
        font-family: var(--display); font-weight: 700; font-size: 1.06rem;
        letter-spacing: 0.01em; color: var(--bone);
        border-top: 1px solid var(--well-3);
        padding-top: 0.55rem; margin: 1.9rem 0 0.85rem 0;
        display: flex; align-items: baseline; gap: 0.6rem;
    }

    .eyebrow {
        font-family: var(--data); font-size: 0.66rem; letter-spacing: 0.16em;
        text-transform: uppercase; color: var(--bone-dim);
    }

    /* Actions are copper; nothing else is. */
    .stButton > button {
        font-family: var(--body); font-weight: 600; border-radius: 3px;
        border: 1px solid var(--well-3); background: transparent;
        color: var(--bone); transition: border-color .15s, color .15s;
    }
    .stButton > button:hover { border-color: var(--copper); color: var(--copper); }
    .stButton > button[kind="primary"] {
        background: var(--copper); border-color: var(--copper); color: #16110C;
    }
    .stButton > button[kind="primary"]:hover { background: var(--copper-lo); color: var(--bone); }

    .stTextInput input, .stTextArea textarea, .stNumberInput input, .stSelectbox div[data-baseweb] {
        background: var(--well-2) !important; border-radius: 3px !important;
        border-color: var(--well-3) !important; color: var(--bone) !important;
    }
    .stTextArea textarea, .stTextInput input { font-family: var(--body); }

    [data-testid="stSidebar"] {
        background: #081D1F; border-right: 1px solid var(--well-3);
    }
    [data-testid="stSidebarNav"] a span { font-family: var(--body); }
    /* The rule above catches every span inside a nav link, including the
       icon span Streamlit renders for :material/ icons -- which turns the
       icon back into literal ligature text ("explore") instead of a glyph.
       Re-assert the font Streamlit itself loads for that one span. */
    [data-testid="stIconMaterial"] { font-family: "Material Symbols Rounded" !important; }

    div[data-testid="stExpander"] details {
        background: var(--well-2); border: 1px solid var(--well-3); border-radius: 4px;
    }
    [data-testid="stDataFrame"] { border: 1px solid var(--well-3); border-radius: 4px; }
    hr { border-color: var(--well-3); }
    code, .stCode { font-family: var(--data) !important; }
    a { color: var(--thread); }

    /* Keyboard focus must stay visible against the dark ground. */
    :focus-visible { outline: 2px solid var(--copper) !important; outline-offset: 2px; }

    /* Live pipeline stepper. Six agents run in sequence and each one can
       take a minute, so the run has to say where it is; a spinner would
       just say "something is happening". Done steps stay legible rather
       than fading out, because the sequence itself is the explanation of
       what this system does. */
    .np-stepper {
        background: var(--well-2); border: 1px solid var(--well-3);
        border-radius: 4px; padding: .7rem .8rem; margin: .6rem 0;
    }
    .np-stepper-pills { display: flex; flex-wrap: wrap; gap: .38rem; }
    .np-step {
        font-family: var(--data); font-size: .68rem; letter-spacing: .06em;
        text-transform: uppercase; padding: .2rem .5rem; border-radius: 2px;
        border: 1px solid transparent;
    }
    .np-step-pending { color: #5C7472; border-color: var(--well-3); }
    .np-step-done    { color: var(--thread); border-color: rgba(111,211,192,.28); }
    .np-step-active  {
        color: #16110C; background: var(--copper); border-color: var(--copper);
        font-weight: 600;
    }
    .np-stepper-message {
        font-family: var(--body); font-size: .82rem; color: var(--bone-dim);
        margin-top: .5rem;
    }

    /* ── Signature: the fate thread ────────────────────────────────────────
       A forecast is a range, not a number, and the Norns' thread is exactly
       that shape. Each clip's p10–p90 reach range is drawn as a thread with
       a knot at the median; once published, the real view count lands as a
       bead. Whether the bead sits on the thread is the whole cross-validation
       story, readable at a glance and without a chart library. */
    .thread-wrap { margin: 0.5rem 0 0.9rem 0; }
    .thread-scale {
        position: relative; height: 1rem;
        font-family: var(--data); font-size: 0.68rem; color: var(--bone-dim);
        margin-top: 0.28rem;
    }
    .thread-scale span { position: absolute; white-space: nowrap; }
    .thread-note {
        font-family: var(--body); font-size: 0.78rem; color: var(--bone-dim);
        margin-top: 0.15rem;
    }

    /* Streamlit's alert defaults are Streamlit-blue/red/orange -- not one
       of them is in this app's palette, which breaks the rule stated above
       the CHART_COLORS block: copper is the only warm accent, mint the
       only "this is real" signal, nothing else is allowed to be a colour.
       Alerts get the same well-2/well-3 card treatment as the pipeline
       stepper and every other panel, with the severity carried only in the
       icon and the lead phrase -- not as a full-box tint borrowed from
       Streamlit's own theme. */
    [data-testid="stAlertContainer"] {
        background: var(--well-2) !important;
        border: 1px solid var(--well-3) !important;
        border-radius: 4px !important;
    }
    [data-testid="stAlertContainer"] [data-testid="stMarkdownContainer"] {
        color: var(--bone-dim);
    }
    [data-testid="stAlertContentInfo"] [data-testid="stAlertDynamicIcon"],
    [data-testid="stAlertContentInfo"] [data-testid="stMarkdownContainer"] strong {
        color: var(--thread) !important;
    }
    [data-testid="stAlertContentWarning"] [data-testid="stAlertDynamicIcon"],
    [data-testid="stAlertContentWarning"] [data-testid="stMarkdownContainer"] strong {
        color: var(--warn) !important;
    }
    [data-testid="stAlertContentError"] [data-testid="stAlertDynamicIcon"],
    [data-testid="stAlertContentError"] [data-testid="stMarkdownContainer"] strong {
        color: var(--error) !important;
    }
    [data-testid="stAlertContentSuccess"] [data-testid="stAlertDynamicIcon"],
    [data-testid="stAlertContentSuccess"] [data-testid="stMarkdownContainer"] strong {
        color: var(--thread) !important;
    }
</style>
""", unsafe_allow_html=True)

# Ordered stage keys emitted by VerdandiOrchestrator.orchestrate_generation's
# progress_callback (see agent/verdandi_orchestrator.py) -> short pill
# labels for the live pipeline stepper. Order here is purely the pill
# DISPLAY order, not an assumption about when each fires -- a multi-clip
# run revisits bragi/heimdall/mimir/skuld/urdr_log once per clip, which
# _render_pipeline_stepper handles by tracking "ever seen" rather than
# "furthest reached".
def _fmt_views(n: float) -> str:
    """Compact view counts. 1.2M reads faster than 1,203,441 at a glance."""
    n = float(n)
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= limit:
            return f"{n / limit:.1f}".rstrip("0").rstrip(".") + suffix
    return f"{n:.0f}"


def _humanize_hook_type(slug: str) -> str:
    """
    "problem_agitation" -> "Problem agitation" for chart axes and legends.

    Deliberately display-only: `hook_type` stays a raw snake_case value
    everywhere it's an identifier rather than a chart label — the
    provenance panel's mono-font `d.choice` and the clip card's backtick
    tag are showing the actual database value on purpose, which is the
    point of a provenance panel. This is only for the two hook_type charts,
    where a label has to be read at a glance rather than looked up.
    """
    if not slug:
        return slug
    return slug.replace("_", " ").capitalize()


_CLIP_ID_PREFIXES = ("gen_trend_", "batch0_clip_", "batch1_clip_", "batch2_clip_", "clip_")
_CLIP_ID_TIMESTAMP = re.compile(r"_?\d{8}-\d{6}_?")


def _humanize_clip_id(clip_id: str) -> str:
    """
    Best-effort display title for a published clip that has no real title
    to show: "gen_trend_sloptokdaily_20260826-103204_finished" ->
    "Sloptokdaily". Worse than the actual hook_title, better than the raw
    slug -- and the only option here, because hook_title lives only in a
    local sidecar JSON that .gcloudignore and .dockerignore both keep out
    of the deployed image (verified, not assumed: neither file nor a real
    output_clips/ copy reaches Cloud Build). A filesystem lookup would
    silently return nothing on every deployed clip, not just old ones.
    """
    if not clip_id:
        return clip_id
    cleaned = clip_id
    for prefix in _CLIP_ID_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    cleaned = cleaned.removesuffix("_finished")
    cleaned = _CLIP_ID_TIMESTAMP.sub("", cleaned)
    cleaned = cleaned.strip("_-").replace("_", " ").replace("-", " ").strip()
    return cleaned.capitalize() if cleaned else clip_id


# One `:material/` icon is enough to make Streamlit load "Material Symbols
# Rounded" for the whole app (used already by the nav in st.Page(icon=...)),
# so a plain HTML span in that font renders correctly wherever unsafe_allow_html
# markup is built by hand instead of going through Streamlit's own icon="" params.
def _material_icon(name: str, size: str = "1.05rem") -> str:
    return (f"<span style=\"font-family:'Material Symbols Rounded';"
            f"font-weight:400;font-size:{size};vertical-align:-0.15em;"
            f"line-height:1;\">{name}</span>")


# The keycap emoji (1️⃣2️⃣3️⃣) Create's three stages used to number themselves
# render as a coloured sticker on most platforms -- blue box, white glyph --
# which is exactly the kind of colour the rule above CHART_COLORS says
# nothing but copper/thread/warn/error is allowed to be. The steps are a
# real sequence (source -> transcript -> output), so the number itself
# stays; only the rendering moves onto the app's own tokens.
def _step_badge(n: int) -> str:
    return (f"<span style=\"display:inline-flex;align-items:center;justify-content:center;"
            f"width:1.3em;height:1.3em;border-radius:3px;background:var(--well-2);"
            f"border:1px solid var(--well-3);color:var(--thread);font-family:var(--data);"
            f"font-size:0.8em;font-weight:600;vertical-align:-0.05em;\">{n}</span>")


# Hand-drawn, not traced from the brand-board JPEGs someone dropped in the
# repo -- those are AI moodboard exports (grid, fake nav links, "ITERATION
# 7" labels, file-format badges baked into the raster), not an isolated
# mark on its own. NornPulse's is the same bead-on-a-thread the hero
# graphic and fate_thread() draw, in the same two tokens the whole palette
# already reserves for exactly this (copper = human/action, thread =
# measured/real). NornLabs' is two interlocking hexagons in the same pair,
# echoing the source board's own two-hex "N" without its specific
# colourway. Both take a target width and derive height from their own
# fixed aspect ratio, so callers can size them to match a text line
# without the two marks drifting out of proportion with each other.
def _nornpulse_mark(width: int) -> str:
    # One crossing, not two: the original spanned a 2:1 viewBox, which
    # meant "bigger" and "narrower" pulled against each other -- scaling
    # it up always meant scaling it wide. Square-ish now, so it can go
    # bigger without also going wider, and it sits closer to NornLabs'
    # own footprint when the two are paired.
    h = round(width * 30 / 32)
    return (f"<svg width='{width}' height='{h}' viewBox='0 0 32 30'>"
            "<path d='M2,15 C11,2 17,28 28,15' fill='none' "
            "stroke='var(--thread)' stroke-width='3' stroke-linecap='round'/>"
            "<path d='M2,15 C11,28 17,2 28,15' fill='none' "
            "stroke='var(--copper)' stroke-width='3' stroke-linecap='round'/>"
            "<circle cx='2' cy='15' r='3' fill='var(--bone)'/>"
            "<circle cx='28' cy='15' r='3' fill='var(--bone)'/>"
            "</svg>")


def _nornlabs_mark(width: int) -> str:
    h = round(width * 40 / 40)
    return (f"<svg width='{width}' height='{h}' viewBox='0 0 40 40'>"
            "<path d='M13,3 L21.66,8 L21.66,18 L13,23 L4.34,18 L4.34,8 Z' fill='none' "
            "stroke='var(--copper)' stroke-width='2'/>"
            "<path d='M27,17 L35.66,22 L35.66,32 L27,37 L18.34,32 L18.34,22 Z' fill='none' "
            "stroke='var(--thread)' stroke-width='2'/>"
            "</svg>")


def fate_thread(p10: float, p50: float, p90: float,
                actual: Optional[float] = None, label: str = "") -> str:
    """
    The forecast drawn as a thread: p10 to p90 with a knot at the median,
    and the real outcome as a bead once there is one.

    A forecast is a range, and a single number misrepresents a distribution
    this heavy-tailed — p90 here is routinely six times p50. Drawn this way
    the question "did reality land inside the predicted range" is answerable
    by looking, which is the entire point of the cross-validation and is
    otherwise buried in a log-log scatter.

    Log scale, because reach spans orders of magnitude. Returns SVG.
    """
    import math

    # No padding beyond p10/p90: it used to be lo*0.6/hi*1.7 so the end
    # ticks had breathing room, but that pushed x10 to ~17% and left the
    # whole bar looking indented rather than flush with everything else
    # on the page. The 4-96% margin in x() below is enough on its own to
    # keep the tick strokes and end labels from clipping at the true edge.
    lo = max(min([v for v in (p10, p50, p90, actual or p50) if v and v > 0] or [1]), 1)
    hi = max([v for v in (p10, p50, p90, actual or p50) if v] or [10])

    def x(value: float) -> float:
        if not value or value <= 0:
            return 0.0
        span = math.log10(hi) - math.log10(lo)
        return 4 + 92 * (math.log10(value) - math.log10(lo)) / (span or 1)

    x10, x50, x90 = x(p10), x(p50), x(p90)
    bead = ""
    if actual and actual > 0:
        xa = x(actual)
        inside = p10 <= actual <= p90
        colour = "var(--thread)" if inside else "var(--warn)"
        bead = (
            f'<circle cx="{xa}%" cy="15" r="5.5" fill="{colour}" stroke="var(--well)" stroke-width="2"/>'
            f'<text x="{xa}%" y="34" fill="{colour}" font-size="9.5" font-family="IBM Plex Mono, monospace" '
            f'text-anchor="middle">{_fmt_views(actual)}</text>'
        )

    return f"""
    <div class="thread-wrap">
      <svg width="100%" height="40" role="img" aria-label="{label or 'forecast range'}">
        <line x1="{x10}%" y1="15" x2="{x90}%" y2="15"
              stroke="var(--well-3)" stroke-width="3" stroke-linecap="round"/>
        <line x1="{x10}%" y1="9" x2="{x10}%" y2="21" stroke="var(--bone-dim)" stroke-width="1.5"/>
        <line x1="{x90}%" y1="9" x2="{x90}%" y2="21" stroke="var(--bone-dim)" stroke-width="1.5"/>
        <circle cx="{x50}%" cy="15" r="3.5" fill="var(--bone-dim)"/>
        {bead}
      </svg>
      <div class="thread-scale">
        <span style="left:{x10}%;">{_fmt_views(p10)}</span>
        <span style="left:{x50}%;">median {_fmt_views(p50)}</span>
        <span style="left:{x90}%;transform:translateX(-100%);">{_fmt_views(p90)}</span>
      </div>
    </div>"""


# =========================================================================
# DEMO MODE
# =========================================================================
# The submission needs a URL a judge can open, which means --allow-
# unauthenticated. Everything that writes or spends therefore has to be
# closed off: the SQL console runs user SQL with write access enabled and
# remoteSecure() available, and every generate button spends real Gemini,
# Lyria and Imagen credit with no ceiling.
#
# The point is not to hide the product. A judge sees every page, every
# chart, every grounded decision and every clip already produced — only
# the actions that would write to ClickHouse, call a paid API or touch
# YouTube are stood down, each saying so where it stands.
DEMO_MODE = os.getenv("NORNPULSE_DEMO_MODE", "0").lower() in ("1", "true", "yes")


def demo_locked(label: str, explanation: str, key: str, icon: Optional[str] = None) -> bool:
    """
    Render a disabled action that explains itself, or the real control.

    Returns True only when the action should proceed, so call sites read
    `if demo_locked(...)` in place of `if st.button(...)` and cannot
    accidentally run the body. `icon` must match the real st.button()'s own
    icon= (test_guarded_labels_match_their_real_buttons only compares the
    label) so the disabled control still looks like the one it replaces.
    """
    if not DEMO_MODE:
        return False
    st.button(label, key=key, disabled=True, help=explanation, icon=icon)
    st.caption(f"{_material_icon('lock', '0.85rem')} {explanation}", unsafe_allow_html=True)
    return True


def demo_banner() -> None:
    if not DEMO_MODE:
        return
    st.info(
        "**Read-only demo.** Every page, chart and grounded decision is live against the "
        "real ClickHouse warehouse and the 4.56-billion-row public dataset. Actions that "
        "would write to the database, spend model credit or publish to YouTube are "
        "disabled — the clips below were produced by this pipeline before deployment.",
        icon=":material/lock:",
    )


# Provenance colours: measured evidence and a seeded assumption must not
# look alike. Mint is already reserved in the palette for things that were
# measured, so it carries over here; copper marks a human/model call, and
# a muted tone marks an assumption so it reads as weaker at a glance
# rather than only on reading.
# Charts were falling back to Plotly's default qualitative palette, which
# has nothing to do with this app's tokens — a chart is data, and the
# palette already says what data looks like here. Mint leads because it is
# the colour reserved for measured values; copper is the human/action
# accent; the rest extend the same family rather than introducing new hues.
CHART_COLORS = ["#6FD3C0", "#C8703C", "#93A8A6", "#D9A441", "#4E9E92", "#8F4E29"]

CHART_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Public Sans, system-ui, sans-serif", color="#EDE6D8", size=12),
    title_font=dict(family="Bricolage Grotesque, sans-serif", size=15),
    xaxis=dict(gridcolor="#17403F", zerolinecolor="#17403F"),
    yaxis=dict(gridcolor="#17403F", zerolinecolor="#17403F"),
    legend=dict(bgcolor="rgba(0,0,0,0)"),
    margin=dict(t=48, b=40, l=8, r=8),
)


def styled(fig):
    """
    Apply the app's palette and chrome to a Plotly figure.

    title_font is applied only when the figure actually has a title:
    setting it on an untitled figure makes Plotly render the string
    "undefined" where the heading would be.
    """
    layout = dict(CHART_LAYOUT)
    title_font = layout.pop("title_font")
    if getattr(fig.layout.title, "text", None):
        layout["title_font"] = title_font
    fig.update_layout(**layout)
    return fig


_PROVENANCE_STYLE = {
    pv.MEASURED: ("var(--thread)", "◆"),
    pv.PRIOR:    ("var(--bone-dim)", "○"),
    pv.MODEL:    ("var(--copper)", "◇"),
}


def render_provenance(clip_meta: dict, key: str) -> None:
    """
    Show where each decision behind a clip came from.

    The pipeline makes seven or eight choices per clip and used to present
    them as a flat list of values, which gave a hook ranked against 9,100
    real videos exactly the same weight as a colour grade read out of a
    sixteen-row table someone typed. Those are different claims.
    """
    decisions = pv.decisions_for_clip(
        clip_meta, subscribers=int(st.session_state.channel_subs),
        facts=_cached_global_facts())
    if not decisions:
        return

    counts = pv.grounding_summary(decisions)
    with st.expander(
        f"How this was decided — {counts[pv.MEASURED]} measured, "
        f"{counts[pv.PRIOR]} assumed, {counts[pv.MODEL]} model", expanded=False
    ):
        for d in decisions:
            colour, mark = _PROVENANCE_STYLE.get(d.level, ("var(--bone-dim)", "·"))
            sample = (f" · <span style='font-family:var(--data);'>n={d.sample:,}</span>"
                      if d.sample else "")
            st.markdown(
                f"<div style='margin:.45rem 0 .7rem 0;'>"
                f"<span style='color:{colour};'>{mark}</span> "
                f"<strong>{d.step}</strong> — "
                f"<span style='font-family:var(--data);'>{d.choice}</span> "
                f"<span style='color:{colour};font-size:.72rem;text-transform:uppercase;"
                f"letter-spacing:.08em;'>{d.label}</span><br>"
                f"<span style='color:var(--bone-dim);font-size:.83rem;'>{d.evidence}{sample}</span>"
                f"</div>", unsafe_allow_html=True)
        st.caption(
            "Measured figures come from the materialised global facts, read within this "
            "channel's size band. Assumed ones come from seeded benchmark tables — the "
            "public dataset carries no visual or audio features, so framing, motion, "
            "colour and score have nothing external to be measured against."
        )


# Each agent is named for a Norse figure and does one job. A judge meeting
# these mid-run has no glossary to hand, so the role travels with the name
# everywhere it appears first — the name is what makes the architecture
# memorable, the role is what makes it legible.
AGENT_ROLES = {
    "urdr": "analytics",
    "verdandi": "reasoning",
    "skuld": "rendering",
    "bragi": "music",
    "heimdall": "cover art",
    "mimir": "narration",
}

PIPELINE_STAGES = [
    ("urdr", "🔮 Urðr · analytics"),
    ("upload", "📤 Upload"),
    ("verdandi", "🧠 Verðandi · reasoning"),
    ("bragi", "🎵 Bragi · music"),
    ("heimdall", "👁️ Heimdall · cover"),
    ("mimir", "🗣️ Mímir · narration"),
    ("skuld", "🎬 Skuld · rendering"),
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
def current_channel():
    """
    The channel the UI is currently reasoning about.

    Subscriber count comes from the number input rather than the registry,
    so a visitor can explore what the advice looks like for a channel of a
    different size without editing config. Everything else — identity,
    profile, and the history the forecast calibrates against — comes from
    the registry entry.
    """
    channel = chans.get_channel(st.session_state.channel_slug)
    return dataclasses.replace(channel, subscribers=int(st.session_state.channel_subs))


@st.cache_data(ttl=300, show_spinner=False)
def _cached_calibrated_forecast(slug: str, subscribers: int, has_subtitles: bool,
                                upload_day: str | None):
    channel = dataclasses.replace(
        chans.get_channel(slug), subscribers=int(subscribers))
    return cal.calibrated_forecast(
        channel, has_subtitles=has_subtitles, upload_day=upload_day,
        facts=_cached_global_facts())


@st.cache_data(ttl=300, show_spinner=False)
def _cached_scoreboard(size_band: str):
    facts = _cached_global_facts()
    return (sb.calibration_summary(size_band, facts=facts),
            sb.scoreboard_frame(size_band, facts=facts))


@st.cache_data(ttl=300, show_spinner="Comparing the benchmark against our own channels…")
def _cached_reality_gap(size_band: str):
    return cal.reality_gap(size_band, facts=_cached_global_facts())


@st.cache_data(ttl=30, show_spinner="Reading hook benchmarks…")
def _cached_hook_benchmarks(_urdr):
    return _urdr.get_hook_type_benchmarks()


@st.cache_data(ttl=30, show_spinner=False)
def _cached_hook_ranking(_urdr, channel_subscribers: int):
    """
    The hook ranking exactly as the generator sees it.

    Not the same order as _cached_hook_benchmarks: that frame is sorted by
    avg_virality_score, and the intelligence summary then reorders it again
    where measured global data disagrees with the seeded prior. Alignment has
    to be scored against the list Verðandi actually chose from, or it reports
    misalignment for a choice that was in fact top-ranked.
    """
    summary = _urdr.get_retention_intelligence_summary(
        channel_subscribers=channel_subscribers)
    return [t["hook_type"] for t in summary.get("hook_taxonomies", [])]


@st.cache_data(ttl=30, show_spinner=False)
def _cached_visual_benchmarks(_urdr):
    """All three visual dimensions in one ClickHouse round-trip."""
    return _urdr.get_all_visual_benchmarks()


@st.cache_data(ttl=15, show_spinner="Reading published outcomes…")
def _cached_published_outcomes(_urdr):
    return _urdr.get_published_outcomes()


@st.cache_data(ttl=60, show_spinner=False)
def _cached_topic_categories(_urdr):
    return _urdr.get_distinct_topic_categories()


# The global layer is materialised, not live — these are cheap local reads,
# but they're still ClickHouse round-trips inside a tab body that Streamlit
# executes on every rerun, so they cache like the rest.
@st.cache_data(ttl=600, show_spinner="Reading the 4.56-billion-row public dataset…")
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
if "channel_slug" not in st.session_state:
    st.session_state.channel_slug = chans.DEFAULT_SLUG
if "channel_subs" not in st.session_state:
    st.session_state.channel_subs = chans.get_channel(chans.DEFAULT_SLUG).subscribers

if "verdandi_adk" not in st.session_state:
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "norn-labs-default")
    st.session_state.verdandi_adk = VerdandiOrchestrator(project_id=project_id)
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

# The masthead belongs to the Home page; a global one would repeat the
# product name above every page and push the actual content below the fold.
# The ClickHouse banner below is deliberately still global — a silently
# degraded connection has to be visible wherever you are.
with st.sidebar:
    # Both marks share one icon column width (28px, flex-shrink:0) so
    # "NornPulse" and "Norn Labs" start at the same x regardless of each
    # mark's own aspect ratio -- inline icon+text left them ragged, since
    # a 44px-wide mark and a 24px-wide one push their labels to different
    # start points even sharing a left edge.
    st.markdown(
        "<div style='padding:.35rem 0 .9rem 0;'>"
        "<div style='display:flex;align-items:center;gap:.5rem;'>"
        f"<span style='display:inline-flex;width:26px;flex-shrink:0;'>{_nornpulse_mark(26)}</span>"
        "<span style='font-family:var(--display);font-weight:800;font-size:1.22rem;"
        "letter-spacing:-.02em;'>NornPulse</span></div>"
        # The parent-brand row links out to nornlabs.ai -- it names the
        # company, not this product, so "go there" is a reasonable click.
        # The mark itself used to share the row's 26px icon column at
        # full size, towering over the 0.66rem eyebrow text next to it;
        # it's sized down to that text's own height and just centered in
        # the same column so both rows still start at the same x.
        "<a href='https://nornlabs.ai' target='_blank' style='text-decoration:none;"
        "color:inherit;display:flex;align-items:center;gap:.5rem;margin-top:.3rem;'>"
        f"<span style='display:inline-flex;width:26px;flex-shrink:0;justify-content:center;'>"
        f"{_nornlabs_mark(13)}</span>"
        "<span class='eyebrow'>Norn Labs</span></a>"
        "</div>", unsafe_allow_html=True)

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
        "**ClickHouse is NOT connected** — Urðr is serving in-memory fallback "
        "benchmarks, so nothing you generate is grounded in (or logged to) real "
        "ClickHouse data.\n\n"
        f"**Reason:** {_urdr_health.connection_error or 'unknown'}"
    )
    if st.button("Retry ClickHouse Connection", key="retry_clickhouse", icon=":material/sync:"):
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


# =========================================================================
# HOME — what the system knows, before it asks for anything
# =========================================================================
def page_home():
    """
    The landing view leads with evidence rather than an input.

    The old entry point was a URL field labelled "YouTube Video Source",
    which asked for work before showing any, and announced a
    YouTube-only constraint the pipeline does not actually have. What
    distinguishes this system is that its choices are grounded in real
    global data, so that is what the first screen shows, with the clips
    already produced as the proof and a single obvious way to make more.
    """
    demo_banner()
    facts = _cached_global_facts()
    subs = int(st.session_state.channel_subs)
    band = gb.size_band_for(subs)
    reach = gb.expected_reach(subs, facts=facts)
    lift = gb.subtitle_lift(band, facts=facts)

    grounded = f"{4_557_605_031:,}"
    # Signature: the NornPulse mark itself, cascading — not the ripples-
    # from-a-well graphic (that's nornlabs.ai's own hero, one level up the
    # brand; repeating it here made the two sites read as one template
    # with the wordmark swapped). Three copies of the same bead-on-thread
    # mark at receding sizes, because it's the one shape this product
    # doesn't share with its parent brand.
    st.markdown(
        "<div style='position:relative;'>"
        "<div aria-hidden='true' style='position:absolute;top:-10px;right:-10px;"
        "width:min(46vw,460px);height:230px;opacity:0.16;pointer-events:none;z-index:0;'>"
        f"<span style='position:absolute;top:6px;right:30px;'>{_nornpulse_mark(120)}</span>"
        f"<span style='position:absolute;top:96px;right:190px;'>{_nornpulse_mark(78)}</span>"
        f"<span style='position:absolute;top:156px;right:300px;'>{_nornpulse_mark(50)}</span>"
        "</div>"
        "<div style='position:relative;z-index:1;'>"
        "<div class='eyebrow'>Norn Labs · autonomous short-form engine</div>"
        "<div style='display:flex;align-items:center;gap:.7rem;margin:.15rem 0 .1rem 0;'>"
        f"{_nornpulse_mark(52)}"
        "<h1 style='margin:0;font-size:2.5rem;line-height:1.03;'>NornPulse</h1></div>"
        f"<p style='color:var(--bone-dim);max-width:56ch;margin:0 0 1.4rem 0;'>"
        f"Every cut, caption and cover is chosen against "
        f"<span style='font-family:var(--data);color:var(--thread);'>{grounded}</span> "
        f"real YouTube videos and a live trending snapshot — not a style guide.</p>"
        "</div></div>",
        unsafe_allow_html=True)

    # The hero is the thread, not a big number: it states the thesis and is
    # the same object used on every clip, so the vocabulary is learned once.
    if reach:
        st.markdown("<div class='eyebrow'>Where videos from a channel your size land</div>",
                    unsafe_allow_html=True)
        st.markdown(
            fate_thread(reach["median_views"] * 0.45, reach["median_views"],
                        reach["median_views"] * 6.0,
                        label="typical reach for this channel size"),
            unsafe_allow_html=True)
        st.markdown(
            f"<div class='thread-note'>Median {reach['median_views']:,.0f} views across "
            f"{reach['sample_videos']:,} real videos from {band}-subscriber channels. "
            f"Your clips are placed on this same thread once they have views.</div>",
            unsafe_allow_html=True)
    else:
        st.info("Run `python seed_global_benchmarks.py` to materialise the global grounding.")

    # The stratification result is the most interesting thing the data
    # produced, and it was three clicks deep. The contrast between bands is
    # the point: the same decision has opposite effects depending on channel
    # size, which is why nothing here is quoted unbanded.
    big = gb.subtitle_lift("100k-1M", facts=facts)
    if lift:
        st.markdown("<div class='workflow-header'>What the data actually says"
                    "<span class='eyebrow'>same decision, opposite answer by channel size</span>"
                    "</div>", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            st.metric(f"Captions · {lift['size_band']} subs",
                      f"{lift['views_lift_pct']:+.0f}% reach",
                      delta=f"{lift['like_lift_pct']:+.0f}% engagement",
                      help=f"{lift['sample_videos']:,} real videos.")
        with c2:
            if big:
                st.metric("Captions · 100k-1M subs",
                          f"{big['views_lift_pct']:+.0f}% reach",
                          delta=f"{big['like_lift_pct']:+.0f}% engagement",
                          help=f"{big['sample_videos']:,} real videos.")
        st.markdown(
            "<div class='thread-note'>Captioning lifts engagement at every size, but only "
            "buys reach once a channel has an audience — captioned videos skew to channels "
            "that already have one. Every figure here is read within a size band, and "
            "<strong>Skuld (rendering)</strong> burns captions in regardless.</div>",
            unsafe_allow_html=True)

    # The self-criticism, above the fold and immediately after the claim it
    # qualifies. Stating the caption paradox and then quietly shipping a
    # forecast built on the same biased population would be exactly the
    # failure the paradox is about — so the correction sits next to it.
    gap = _cached_reality_gap(band)
    if gap and gap["observed_videos"]:
        st.markdown("<div class='workflow-header'>And the same problem in our own data"
                    "<span class='eyebrow'>banding by size does not remove survivorship bias</span>"
                    "</div>", unsafe_allow_html=True)
        g1, g2, g3 = st.columns(3)
        with g1:
            st.metric("Benchmark says", f"{gap['predicted_median_views']:,.0f} views",
                      help=f"Median for {band}-subscriber channels across "
                           f"{gap['benchmark_sample_videos']:,} videos in the public dataset.")
        with g2:
            st.metric("Real channels get", f"{gap['observed_median_views']:,.0f} views",
                      help=f"Measured across {gap['observed_videos']} videos from real "
                           f"channels in this band with full published history.")
        with g3:
            st.metric("Overstated by",
                      f"{1 / gap['ratio']:,.0f}×" if gap["ratio"] else "—",
                      help="How far the population figure sits above observed reality. "
                           f"Measured on {gap['observed_videos']} videos, which is thin.")
        st.markdown(
            "<div class='thread-note'>The public dataset is a crawl — it only holds videos "
            "discoverable enough to be crawled, so a channel posting into the void isn't in "
            "it. Forecasts here are calibrated against a channel's own history instead, with "
            "the uncalibrated figure kept alongside so the correction stays visible.</div>",
            unsafe_allow_html=True)

    # One worked example of provenance, above the fold. A judge should not
    # have to open a clip card to learn that the system distinguishes what
    # it measured from what it assumed.
    clips_for_example = rq.list_clips(rq.APPROVED)
    example = next((c for c in clips_for_example if c["metadata"].get("hook_type")), None)
    if example:
        decisions = pv.decisions_for_clip(
            example["metadata"], int(st.session_state.channel_subs), facts)
        counts = pv.grounding_summary(decisions)
        st.markdown("<div class='workflow-header'>How a clip gets decided"
                    f"<span class='eyebrow'>{counts[pv.MEASURED]} measured · "
                    f"{counts[pv.PRIOR]} assumed · {counts[pv.MODEL]} model judgement</span>"
                    "</div>", unsafe_allow_html=True)
        shown = [d for d in decisions if d.level == pv.MEASURED][:1] + \
                [d for d in decisions if d.level == pv.PRIOR][:1]
        for d in shown:
            colour, mark = _PROVENANCE_STYLE.get(d.level, ("var(--bone-dim)", "·"))
            sample = f" · n={d.sample:,}" if d.sample else ""
            st.markdown(
                f"<div style='margin:.3rem 0 .6rem 0;'>"
                f"<span style='color:{colour};'>{mark}</span> <strong>{d.step}</strong> — "
                f"<span style='font-family:var(--data);'>{d.choice}</span> "
                f"<span style='color:{colour};font-size:.72rem;text-transform:uppercase;"
                f"letter-spacing:.08em;'>{d.label}</span><br>"
                f"<span style='color:var(--bone-dim);font-size:.83rem;'>{d.evidence}{sample}"
                f"</span></div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='thread-note'>Framing, motion, colour and music are marked assumed — "
            "the public dataset carries no visual or audio features to measure them against. "
            "Full breakdown on the Review page.</div>", unsafe_allow_html=True)

    st.markdown("<div class='workflow-header'>Your clips</div>", unsafe_allow_html=True)
    counts = rq.state_counts()
    clips = rq.list_clips()
    outcomes_home = _cached_published_outcomes(st.session_state.verdandi_adk.urdr)
    published_home = (
        outcomes_home[~outcomes_home["video_unavailable"].astype(bool)]
        if not outcomes_home.empty and "video_unavailable" in outcomes_home.columns
        else outcomes_home)

    if not clips and not published_home.empty:
        # This session's local review queue is empty -- nothing was
        # generated here, which is the normal state on the read-only demo
        # -- but the warehouse holds real published output. Showing that
        # instead of an empty state is what makes the banner above ("the
        # clips below were produced by this pipeline before deployment")
        # actually true rather than a promise the page doesn't keep.
        # video_unavailable rows are filtered out for the same reason: a
        # dead YouTube link here would be the identical failure, on the
        # same page, for the same cause.
        st.markdown(
            f"<p style='color:var(--bone-dim);'>{len(published_home)} real clip(s) "
            "published by this pipeline.</p>", unsafe_allow_html=True)
        for _, row in published_home.sort_values("published_at", ascending=False).head(3).iterrows():
            vid = row.get("youtube_video_id")
            c1, c2 = st.columns([1, 3], gap="medium", vertical_alignment="center")
            with c1:
                if vid:
                    st.image(f"https://img.youtube.com/vi/{vid}/hqdefault.jpg", width=170)
            with c2:
                st.markdown(f"**{_humanize_clip_id(row.get('clip_id')) or vid}**")
                bits = []
                if row.get("hook_type"):
                    bits.append(f"`{_humanize_hook_type(row['hook_type'])}`")
                views = row.get("actual_view_count")
                if views and views > 0:
                    bits.append(f"{int(views):,} views")
                else:
                    # A bare "0 views" reads as a failed clip rather than
                    # one too young to have views yet -- the same
                    # distinction the Intelligence scoreboard already
                    # makes for the same data.
                    age_days = None
                    if row.get("published_at") is not None:
                        try:
                            age_days = (pd.Timestamp.utcnow().tz_localize(None)
                                        - pd.to_datetime(row["published_at"])).total_seconds() / 86400
                        except Exception:
                            age_days = None
                    if age_days is not None and gb.too_early_to_judge(float(age_days), band, facts):
                        bits.append("too young to have views yet")
                    else:
                        bits.append("not yet synced")
                if row.get("youtube_url"):
                    bits.append(f"[watch]({row['youtube_url']})")
                st.caption(" · ".join(bits))
    elif not clips:
        # No local queue AND no real published output (e.g. warehouse
        # unreachable) -- the honest empty state, without a call to
        # action a read-only visitor cannot take. "Make another" below
        # already carries that invitation for anyone who can act on it.
        st.markdown(
            "<p style='color:var(--bone-dim);'>Nothing published yet.</p>",
            unsafe_allow_html=True)
    else:
        m1, m2, m3 = st.columns(3)
        m1.metric("Approved", counts.get(rq.APPROVED, 0))
        m2.metric("Awaiting review", counts.get(rq.PENDING, 0))
        outcomes = _cached_published_outcomes(st.session_state.verdandi_adk.urdr)
        if not outcomes.empty and "video_unavailable" in outcomes.columns:
            live = outcomes[~outcomes["video_unavailable"].astype(bool)]
            m3.metric("Best real reach", f"{int(live['actual_view_count'].max()):,} views"
                      if not live.empty else "—")

        # Approved first, then anything still pending. Showing the newest
        # regardless of state put rejected clips — including a near-duplicate
        # title — at the top of the page, directly under a metric saying
        # three were approved.
        showcase = ([c for c in clips if c["state"] == rq.APPROVED]
                    + [c for c in clips if c["state"] == rq.PENDING])
        for clip in showcase[:3]:
            meta = clip["metadata"]
            # A 9:16 thumbnail at this width runs ~300px tall against two
            # short lines of text -- top-aligned columns left the text
            # stranded at the top with dead space below it.
            c1, c2 = st.columns([1, 3], gap="medium", vertical_alignment="center")
            with c1:
                if clip["thumbnail_path"]:
                    st.image(clip["thumbnail_path"], width=170)
            with c2:
                st.markdown(f"**{meta.get('hook_title') or _humanize_clip_id(clip['clip_id'])}**")
                bits = [clip["state"]]
                if meta.get("hook_type"):
                    bits.append(f"`{_humanize_hook_type(meta['hook_type'])}`")
                if (clip.get("decision") or {}).get("youtube_url"):
                    bits.append(f"[watch]({clip['decision']['youtube_url']})")
                st.caption(" · ".join(bits))

    st.markdown("<div class='workflow-header'>Make another</div>", unsafe_allow_html=True)
    st.markdown(
        "<p style='color:var(--bone-dim);max-width:52ch;'>Any 16:9 source works — a local file "
        "or a link. NornPulse finds the moments worth cutting, scores them against the global "
        "data, and stages them for your approval.</p>", unsafe_allow_html=True)
    if st.button("New clip", type="primary", key="home_new"):
        st.switch_page(_PAGE_CREATE)



def page_create():
    """Ingest a source, set the controls, run the pipeline."""
    demo_banner()

    # --- 1. Source ---
    with st.container():
        st.markdown(f"<div class='workflow-header'>{_step_badge(1)} Source"
                    "<span class='eyebrow'>a link, or a file from anywhere</span></div>", unsafe_allow_html=True)
        # Ingestion is a spend path in its own right: pasting a link starts a
        # download and then a Gemini transcription immediately, before the
        # Execute button is ever pressed. The demo gate covered generation
        # and missed this entirely.
        #
        # It also cannot work here. YouTube bot-blocks datacenter IPs
        # ("Sign in to confirm you're not a bot"), so yt-dlp fails from Cloud
        # Run regardless of credit. Better to say that than to offer a field
        # that always errors.
        # Upload works everywhere; a link does not. YouTube bot-blocks
        # datacenter IPs, so yt-dlp cannot run from Cloud Run at all — an
        # uploaded file is the only way a visitor can drive the real
        # pipeline, and it skips the download and its cost entirely.
        uploaded = st.file_uploader(
            "Upload a 16:9 video", type=["mp4", "mov", "m4v", "webm"],
            help="Any source works — NornPulse reads the video, not where it came from.")
        uploaded_path = None
        if uploaded is not None:
            uploads = Path("output_clips/uploads")
            uploads.mkdir(parents=True, exist_ok=True)
            # Named from the content hash so re-uploading the same file
            # reuses its transcript instead of paying to transcribe twice.
            digest = hashlib.sha256(uploaded.getbuffer()).hexdigest()[:16]
            uploaded_path = uploads / f"upload_{digest}{Path(uploaded.name).suffix}"
            if not uploaded_path.exists():
                uploaded_path.write_bytes(uploaded.getbuffer())
            st.caption(f"📁 {uploaded.name} · {uploaded_path.stat().st_size / 1048576:.0f} MB")

        if DEMO_MODE:
            st.text_input(
                "Video link", key="yt_url_locked", disabled=True,
                placeholder="Links are disabled here — upload a file instead")
            st.caption(
                "🔒 Link ingestion cannot work from Cloud Run: YouTube blocks "
                "datacenter IPs. Upload a file above to run the real pipeline."
            )
            yt_url = ""
        else:
            yt_url = st.text_input(
                "Video link", key="yt_url",
                placeholder="Paste a video URL — or upload a file above",
                help="Any link yt-dlp can resolve. NornPulse works on the video, "
                     "not on where it came from.")
        active_video_path = str(uploaded_path) if uploaded_path else None

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
                        st.video(active_video_path, width=440)
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

        with st.expander(f"Batch Mode (channel/playlist, up to {BATCH_MAX_VIDEOS} videos)",
                         icon=":material/folder_copy:"):
            st.caption(
                f"Runs the full pipeline once per video (capped at {BATCH_MAX_VIDEOS} — each is a real "
                "Gemini + Lyria + image + TTS generation), then ranks every resulting clip by predicted "
                "virality score in the Review & Publish column. Uses its own fixed style defaults rather "
                "than Column 2's sliders, since those aren't set yet at this point in the layout."
            )
            batch_url = st.text_input("YouTube channel or playlist URL:", key="batch_url")
            batch_content_hint = st.text_input(
                "Creative Direction (optional)", key="batch_content_hint",
                placeholder="e.g. a romantic moment, a tense confrontation...",
                icon=":material/movie:",
            ).strip() or None
            batch_caption_language = st.text_input(
                "Translate Captions (optional)", key="batch_caption_language",
                placeholder="e.g. English — leave blank to keep the source language",
                icon=":material/translate:",
            ).strip() or None
            if demo_locked("Run Batch", 'Runs the real pipeline against paid Gemini, Lyria and Imagen APIs — disabled on the public demo.', "run_batch_locked", icon=":material/folder_copy:"):
                pass
            elif st.button("Run Batch", key="run_batch", icon=":material/folder_copy:"):
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
                                channel_subscribers=int(st.session_state.channel_subs),
                    caption_font=caption_font_choice,
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
            st.markdown(f"<div class='workflow-header'>{_material_icon('publish')} "
                        "Recently Published</div>", unsafe_allow_html=True)
            for pub in reversed(st.session_state.recently_published[-5:]):
                st.markdown(f"🔗 [{pub['title']}]({pub['url']}) · `{pub['privacy_status']}`")

    # --- 2. Transcript and controls ---
    st.divider()
    with st.container():
        st.markdown(f"<div class='workflow-header'>{_step_badge(2)} Transcript &amp; controls</div>", unsafe_allow_html=True)

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

        target_clips = st.slider(
            "Clips to generate", min_value=1, max_value=3, value=1,
            help="Verðandi picks this many distinct moments from the transcript. "
                 "Each one costs a full pipeline run.")

        # Everything below shapes WHICH moment gets picked or steers a
        # secondary creative dimension — caption style included — rather
        # than being needed for every run, so it's tucked away here and the
        # always-visible controls above stay scannable at a glance.
        transcript_window = None
        auto_window_mode = "random"
        with st.expander("Advanced Settings", icon=":material/tune:"):
            # Every face here is installed in the image and checked at build
            # time. libass substitutes silently for anything it cannot
            # resolve, so an unlisted name would change the look of the
            # render with nothing logged.
            caption_font_choice = st.selectbox(
                "Caption typeface", list(SkuldCaptionFonts),
                help="Burned into the video by libass. All options ship in the "
                     "container; the build fails if one is missing.")
            warmth = st.slider(
                "🌡️ Warmth", min_value=0.0, max_value=1.0, value=0.5, step=0.05,
                help="Cool blue/white captions at 0.0 → warm gold/orange color grade at 1.0",
            )
            crazy = st.slider(
                "Crazy", min_value=0.0, max_value=1.0, value=0.3, step=0.05,
                help="Controls both the reveal pace and the pop: ~5-word phrases with a gentle "
                     "bounce at 0.0 → rapid single-word pops with scale overshoot and wobble at 1.0.",
            )
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
                "Creative Direction (optional)",
                key="content_hint",
                placeholder="e.g. a romantic moment, a tense confrontation, a funny reaction...",
                help="Free-text steer for WHICH moment gets picked. Verðandi prioritizes a genuine match "
                     "over a marginally higher virality score — leave blank to let it pick freely.",
                icon=":material/movie:",
            ).strip() or None

            caption_language = st.text_input(
                "Translate Captions (optional)",
                key="caption_language",
                icon=":material/translate:",
                placeholder="e.g. English, Spanish — leave blank to keep the source language",
                help="Burns in captions translated into this language instead of the source transcript's "
                     "own language. Timing is unaffected — only the on-screen words change. Verðandi's "
                     "reasoning and Mímir's narration fallback still use the original-language transcript.",
            ).strip() or None

            cut_energy = st.slider(
                "Cut Energy", min_value=0.0, max_value=1.0, value=0.5, step=0.05,
                help="Biases the target clip length within the duration range: calm at 0.0 leans "
                     "toward the longer end (let the moment breathe), energetic at 1.0 leans toward "
                     "the shorter end (snappy cut). A bias, not a hard override — the min/max range "
                     "itself is still always enforced.",
            )

        # Allowed on the demo only with an uploaded file. Without one there is
        # no source to work from here anyway, since links cannot be fetched.
        generate_clicked = (
            False
            if (DEMO_MODE and not uploaded_path and demo_locked(
                "EXECUTE PIPELINE",
                "Upload a video above to run the pipeline.",
                "execute_locked",
                icon=":material/bolt:",
            ))
            else st.button("EXECUTE PIPELINE", type="primary", icon=":material/bolt:")
        )

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
                    channel_subscribers=int(st.session_state.channel_subs),
                    # What this clip was cut from. A link where there is
                    # one, the file name where there isn't — NornPulse
                    # works on the video, not on where it came from, so
                    # both cases have to record something.
                    source_ref=yt_url or (
                        Path(active_video_path).name if active_video_path else None),
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
                st.success("Execution complete!")
            except Exception as e:
                progress_placeholder.empty()
                st.error(f"Pipeline execution failed: {e}")

    # --- 3. What this run produced ---
    # Deliberately not a second review queue: this is the immediate result
    # of the run you just started, with its forecast. Everything ever
    # generated, and every decision made, lives on the Review page.
    st.divider()
    with st.container():
        st.markdown(f"<div class='workflow-header'>{_step_badge(3)} This run's output"
                    "<span class='eyebrow'>every clip and decision lives on the Review page</span>"
                    "</div>", unsafe_allow_html=True)

        if not st.session_state.current_generation:
            st.markdown(
                "<p style='color:var(--bone-dim);'>Nothing generated in this session yet. "
                "Set a source above and run the pipeline — clips appear here as they finish, "
                "and stay on the Review page afterwards.</p>", unsafe_allow_html=True)
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

                forecast = _cached_calibrated_forecast(
                    st.session_state.channel_slug,
                    int(st.session_state.channel_subs),
                    bool(item.get("has_subtitles")),
                    st.session_state.get("planned_upload_day") or None,
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
                    st.success(f"Grounded pick: **{hook_type}** (Urðr's #{hook_rank} ranked hook)")
                elif hook_rank is not None:
                    st.warning(f"**{hook_type}** ranks #{hook_rank} in Urðr's benchmarks — top pick was **{top_hook}**")
                else:
                    st.caption(f"Hook type: {hook_type} (not found in Urðr's benchmark taxonomy)")

                render_provenance(item, key=f"prov_run_{c_id}")

                similar_df = _cached_similar_shorts(st.session_state.verdandi_adk.urdr, hook_type)
                if not similar_df.empty:
                    with st.expander(f"Similar historical '{hook_type}' shorts",
                                     icon=":material/bar_chart:"):
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
                    if demo_locked("Publish", 'Publishes to a real YouTube channel — disabled on the public demo.', f"pub_locked_{c_id}", icon=":material/rocket_launch:"):
                        pass
                    elif st.button("Publish", key=f"pub_{c_id}", type="primary", icon=":material/rocket_launch:"):
                        with st.spinner("Publishing..."):
                            try:
                                result = st.session_state.publisher.upload_to_youtube_shorts(
                                    c_path, t_val, d_val, privacy_status=privacy_choice,
                                    thumbnail_path=item.get("thumbnail_path"),
                                    clip=item,
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
                                st.success(f"Published: [{result['url']}]({result['url']}) · {result['privacy_status']}{thumb_note}")
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
                                st.error(f"Publish failed: {e}")
                with b2:
                    if demo_locked("Reject", 'Writes to the shared ClickHouse warehouse — disabled on the public demo.', f"rej_locked_{c_id}", icon=":material/close:"):
                        pass
                    elif st.button("Reject", key=f"rej_{c_id}", icon=":material/close:"):
                        rq.record_decision(c_id, rq.REJECTED, comment, source="ui")
                        moved = rq.archive_rejected(c_id)
                        st.session_state.current_generation.pop(idx)
                        st.warning(
                            f"Rejected — {len(moved)} file(s) archived to output_clips/rejected/."
                            + (" Comment recorded." if comment.strip() else "")
                        )
                        st.rerun()



def page_review():
    """The durable queue: every clip and the decision against it."""
    demo_banner()
    st.markdown(f"<div class='workflow-header'>{_material_icon('video_library')} "
                "Review Queue &amp; Library</div>", unsafe_allow_html=True)

    # One view over three directories plus the decision ledger. The old
    # version globbed output_clips/*_9x16.mp4 non-recursively, which stopped
    # working the moment rejection began archiving into a subdirectory —
    # archived clips were safe on disk but invisible here.
    counts = rq.state_counts()
    st.caption(
        f"**{counts.get(rq.PENDING, 0)}** awaiting review · "
        f"**{counts.get(rq.APPROVED, 0)}** approved · "
        f"**{counts.get(rq.REJECTED, 0)}** rejected. "
        "Decisions made here and by email reply share one ledger, so this list "
        "is the same either way."
    )

    # :material/name: is the same icon-shorthand the page nav and every
    # button on this page now use -- st.radio renders it in option labels
    # too, so the filter row doesn't have to fall back to raw emoji just
    # because it isn't a button.
    filter_labels = {
        f":material/hourglass_empty: Pending ({counts.get(rq.PENDING, 0)})": rq.PENDING,
        f":material/check_circle: Approved ({counts.get(rq.APPROVED, 0)})": rq.APPROVED,
        f":material/close: Rejected ({counts.get(rq.REJECTED, 0)})": rq.REJECTED,
        "All": None,
    }
    chosen = st.radio("Show", list(filter_labels), horizontal=True,
                      label_visibility="collapsed", key="library_filter")
    clips = rq.list_clips(state=filter_labels[chosen])

    if not clips:
        st.info("Nothing here yet. Generate clips from the Create tab.")

    for clip in clips:
        meta = clip["metadata"]
        decision = clip["decision"]
        cid = clip["clip_id"]
        title = meta.get("hook_title") or cid

        badge = {rq.PENDING: ":material/hourglass_empty: Pending review",
                 rq.APPROVED: ":material/check_circle: Approved",
                 rq.REJECTED: ":material/close: Rejected"}.get(clip["state"], clip["state"])

        with st.container(border=True):
            head, body = st.columns([1, 2], gap="medium")
            with head:
                st.video(clip["video_path"], width=210)
                if clip["thumbnail_path"]:
                    st.image(clip["thumbnail_path"], width=90, caption="👁️ Heimdall cover")

            with body:
                st.markdown(f"### {title}")
                st.caption(f"{badge} · `{cid}` · {clip['size_mb']} MB · "
                           f"{clip['modified_at'].strftime('%Y-%m-%d %H:%M')} UTC")

                if meta.get("social_caption"):
                    st.markdown(f"*{meta['social_caption']}*")

                # Everything below was already sitting in the sidecar JSON
                # next to the render; the old library showed only a filename.
                facts = []
                if meta.get("virality_score") is not None:
                    facts.append(f"**{meta['virality_score']}**/100 virality")
                if meta.get("hook_type"):
                    rank = meta.get("hook_rank")
                    facts.append(f"hook `{_humanize_hook_type(meta['hook_type'])}`"
                                 + (f" (Urðr #{rank})" if rank else ""))
                if meta.get("start_time"):
                    facts.append(f"cut {meta['start_time']}–{meta.get('end_time', '?')}")
                if facts:
                    st.markdown(" · ".join(facts))

                treatment = [meta.get(k) for k in ("crop_mode", "motion_effect", "color_grade")]
                treatment = [t for t in treatment if t]
                if treatment:
                    st.caption("🎬 " + " · ".join(treatment))

                extras = []
                if meta.get("has_subtitles"):
                    lang = meta.get("caption_language")
                    extras.append(f"💬 subtitles{f' ({lang})' if lang else ''}")
                if meta.get("has_bragi_score"):
                    extras.append(f"🎵 {meta.get('music_genre') or 'original score'}")
                if meta.get("has_narration"):
                    extras.append("🗣️ narration")
                if extras:
                    st.caption(" · ".join(extras))

                if decision:
                    line = (f"Decided **{decision['status']}** via {decision.get('source', '?')} "
                            f"on {decision.get('decided_at', '?')[:16].replace('T', ' ')}")
                    if decision.get("youtube_url"):
                        line += f" — [watch]({decision['youtube_url']})"
                    st.markdown(line)
                    if decision.get("comment"):
                        st.info(decision['comment'])
                    if decision.get("previous"):
                        prev = decision["previous"]
                        st.caption(f"↩️ previously {prev.get('status')} via {prev.get('source', '?')}"
                                   + (f" — “{prev['comment']}”" if prev.get("comment") else ""))

                # --- actions ---
                if clip["state"] == rq.PENDING:
                    comment = st.text_area(
                        "Review comment", key=f"lib_cmt_{cid}", height=68,
                        placeholder="Why this works, or why it doesn't — recorded with either decision.",
                    )
                    a1, a2 = st.columns(2, gap="small")
                    with a1:
                        if demo_locked("Approve", 'Writes to the shared ClickHouse warehouse — disabled on the public demo.', f"lib_app_locked_{cid}", icon=":material/check_circle:"):
                            pass
                        elif st.button("Approve", key=f"lib_app_{cid}", type="primary", icon=":material/check_circle:"):
                            rq.record_decision(cid, rq.APPROVED, comment, source="ui")
                            st.rerun()
                    with a2:
                        if demo_locked("Reject", 'Writes to the shared ClickHouse warehouse — disabled on the public demo.', f"lib_rej_locked_{cid}", icon=":material/close:"):
                            pass
                        elif st.button("Reject", key=f"lib_rej_{cid}", icon=":material/close:"):
                            rq.record_decision(cid, rq.REJECTED, comment, source="ui")
                            rq.archive_rejected(cid)
                            st.rerun()
                    st.caption(
                        "Approving records the decision; publishing to YouTube stays a "
                        "separate, deliberate step."
                    )

                render_provenance(meta, key=f"prov_lib_{cid}")

                # Deletion is two-step and separate from rejection: rejecting
                # archives, because the render cost real API spend and the
                # comment is the useful part. This is the irreversible one.
                confirm_key = f"lib_confirm_{cid}"
                with st.expander("Delete permanently", icon=":material/warning:"):
                    st.caption(
                        "Removes the render, subtitles, thumbnail and metadata from disk. "
                        "This cannot be undone — reject instead if you only want it out of the way."
                    )
                    if st.checkbox("I understand this is permanent", key=confirm_key):
                        if demo_locked("Delete forever", 'Permanently deletes files — disabled on the public demo.', f"lib_del_locked_{cid}"):
                            pass
                        elif st.button("Delete forever", key=f"lib_del_{cid}"):
                            removed = rq.delete_clip(cid, location=clip["location"])
                            st.warning(f"Deleted {len(removed)} file(s) for {cid}.")
                            st.rerun()



def page_intelligence():
    """ClickHouse analytics and the global grounding."""
    demo_banner()
    st.markdown(f"<div class='workflow-header'>{_material_icon('analytics')} "
                "Live ClickHouse Analytics Hub</div>", unsafe_allow_html=True)

    urdr = st.session_state.verdandi_adk.urdr
    connected = urdr.is_connected()

    if not connected:
        # The global banner above the tabs already carries the reason and
        # the retry; this is the local reminder that every chart below is
        # synthetic fallback data rather than anything real.
        st.error(
            "**Every chart on this tab is in-memory fallback data, not real ClickHouse data.** "
            "See the reason and retry at the top of the page."
        )

    benchmarks_df = _cached_hook_benchmarks(urdr)
    if not benchmarks_df.empty and "hook_type" in benchmarks_df.columns:
        # Charts need a label read at a glance; the provenance panel and
        # clip tags elsewhere keep the raw hook_type on purpose, so this
        # column exists only for the two charts below.
        benchmarks_df = benchmarks_df.assign(
            hook_label=benchmarks_df["hook_type"].map(_humanize_hook_type))

    # Published count and alignment used to come from session counters, which
    # meant they read 0 and "—" forever on the read-only demo: nothing
    # generates or publishes there, so the counters never leave their initial
    # value. The warehouse already holds the real history, so read that and
    # keep the session counters only as the offline fallback.
    outcomes_df = _cached_published_outcomes(urdr)

    col_a1, col_a2, col_a3, col_a4, col_a5 = st.columns(5)
    with col_a1: st.metric("ADK Reasoning Engine", "Active 🟢")
    with col_a2:
        published_ids = (int(outcomes_df["youtube_video_id"].nunique())
                          if not outcomes_df.empty and "youtube_video_id" in outcomes_df.columns
                          else 0)
        if published_ids:
            st.metric("Published Shorts", published_ids)
        elif st.session_state.published_count:
            st.metric("Published Shorts", st.session_state.published_count)
        else:
            # "0" here is a lie that looks like a measurement, and it has two
            # distinct causes that both produce it: the warehouse was
            # unreachable, or outcome rows exist but none carry a
            # youtube_video_id yet. Either way it read 0 here while panels
            # further down the same page said "11 of 11 published clip(s)",
            # which is how a reviewer finds out the top of the page is not
            # talking to the same source as the bottom.
            st.metric("Published Shorts", "—")
            reason = ("no video IDs recorded yet" if not outcomes_df.empty
                       else "warehouse unreachable")
            st.caption(f"not measured — {reason}")
    with col_a3:
        # Same rule as Grounding Alignment beside it: an em dash where there
        # is nothing to average. "0.0%" reads as a measured floor rather than
        # an absent benchmark, and this project's whole claim is that it says
        # which is which.
        if benchmarks_df.empty or "avg_3s_retention" not in benchmarks_df.columns:
            st.metric("Avg. 3s Retention", "—")
            st.caption("not measured — no benchmarks loaded")
        else:
            st.metric("Avg. 3s Retention",
                      f"{float(benchmarks_df['avg_3s_retention'].mean()):.1f}%")
    with col_a4:
        st.metric("ClickHouse State", "Connected 🟢" if connected else "Fallback 🟡")
    with col_a5:
        history = list(st.session_state.alignment_history)
        if not history and not outcomes_df.empty and "hook_type" in outcomes_df.columns:
            # Same rule the generator applies per clip: rank <= 2 counts.
            ranked = _cached_hook_ranking(urdr, int(st.session_state.channel_subs))
            top2 = set(ranked[:2])
            history = [h in top2 for h in outcomes_df["hook_type"].dropna()]
        alignment_rate = (100 * sum(history) / len(history)) if history else None
        st.metric(
            "Grounding Alignment",
            f"{alignment_rate:.0f}%" if alignment_rate is not None else "—",
            help="Share of published clips whose chosen hook_type ranked in Urðr's "
                 "top 2 benchmarks. Clips generated in this session count first.",
        )

    if not benchmarks_df.empty:
        # Horizontal, not vertical: eight category labels ("Story in
        # medias res", "Metaphor analogy", ...) rotated 45deg to fit
        # under vertical bars read as skewed rather than legible. A
        # horizontal bar lets every label sit flat, and it's the
        # orientation the hook-pattern chart further down already uses.
        fig = px.bar(
            benchmarks_df.sort_values("avg_3s_retention"),
            y="hook_label",
            x=["avg_3s_retention", "avg_completion_rate"],
            barmode="group", orientation="h",
            template="plotly_dark", color_discrete_sequence=CHART_COLORS,
            title="Seeded hook benchmarks · the prior the pipeline chooses from",
            labels={"value": "Percent", "hook_label": "", "variable": "Metric"},
        )
        st.plotly_chart(styled(fig), width='stretch')

        # Retention drop-off curve: the README describes Urðr tracking
        # 3s/15s/30s drop-off curves per hook type — this is the first
        # place that data is actually visualized.
        curve_df = benchmarks_df.melt(
            id_vars=["hook_label"],
            value_vars=["avg_3s_retention", "avg_15s_retention", "avg_30s_retention"],
            var_name="checkpoint", value_name="retention_pct",
        )
        checkpoint_seconds = {"avg_3s_retention": 3, "avg_15s_retention": 15, "avg_30s_retention": 30}
        curve_df["seconds"] = curve_df["checkpoint"].map(checkpoint_seconds)
        curve_fig = px.line(
            curve_df.sort_values("seconds"),
            x="seconds", y="retention_pct", color="hook_label",
            markers=True, template="plotly_dark", color_discrete_sequence=CHART_COLORS,
            title="Retention Drop-Off Curves by Hook Type",
            labels={"seconds": "Seconds into clip", "retention_pct": "Retention %", "hook_label": "Hook type"},
        )
        st.plotly_chart(styled(curve_fig), width='stretch')

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
        st.markdown(f"<div class='workflow-header'>{_material_icon('public')} "
                    "Global YouTube Grounding</div>", unsafe_allow_html=True)
        st.caption(
            "Three layers live in this warehouse: **global structural facts** materialised from "
            "ClickHouse's public 4.56-billion-row YouTube dataset, a **current trending** snapshot "
            "pulled from the YouTube Data API, and **your own published clips**. The seed benchmarks "
            "above are priors the pipeline chooses from; this is external evidence about whether "
            "those choices are right."
        )

        pick_col, subs_col = st.columns([1, 1])
        with pick_col:
            all_channels = chans.list_channels()
            slugs = [c.slug for c in all_channels]
            titles = {c.slug: f"{c.title} · {c.subscribers:,} subs" for c in all_channels}
            chosen = st.selectbox(
                "Channel", slugs,
                index=slugs.index(st.session_state.channel_slug)
                if st.session_state.channel_slug in slugs else 0,
                format_func=lambda sl: titles.get(sl, sl),
                help="Which channel the figures below are read for. Selects the "
                     "size band, and the published history the forecast is "
                     "calibrated against.",
            )
            if chosen != st.session_state.channel_slug:
                # Follow the newly picked channel's real subscriber count
                # rather than carrying the previous channel's number over.
                st.session_state.channel_slug = chosen
                st.session_state.channel_subs = chans.get_channel(chosen).subscribers
                st.rerun()
        with subs_col:
            subs = st.number_input(
                "Subscriber count", min_value=0, step=10,
                value=int(st.session_state.channel_subs),
                help="Channel size is the dominant confounder in the global data — captioned and "
                     "age-restricted videos skew heavily toward large channels. Every figure below is "
                     "read within the band this number falls into.",
            )
            st.session_state.channel_subs = int(subs)
        band = gb.size_band_for(int(subs))

        # The reality gap. This is the most important number the project
        # has, and it is a criticism of its own grounding layer: the
        # population median for a size band substantially overstates what
        # real channels of that size actually get, because the public
        # dataset is a crawl and only contains videos discoverable enough
        # to have been crawled.
        gap = _cached_reality_gap(band)
        if gap and gap["observed_videos"]:
            ratio = gap["ratio"]
            st.markdown(
                f"<div class='workflow-header' style='margin-top:1.1rem;'>"
                f"⚖️ Benchmark vs reality · {band} subs</div>",
                unsafe_allow_html=True)
            g1, g2, g3 = st.columns(3)
            with g1:
                st.metric("Global benchmark", f"{gap['predicted_median_views']:,.0f} views",
                          help=f"Median for this band across "
                               f"{gap['benchmark_sample_videos']:,} videos in the public dataset.")
            with g2:
                st.metric("Actually observed", f"{gap['observed_median_views']:,.0f} views",
                          help=f"Median across {gap['observed_videos']} real videos from "
                               f"channels in this band that we have full history for.")
            with g3:
                st.metric("Overstatement", f"{1 / ratio:,.0f}×" if ratio else "—",
                          help="How far the population figure sits above observed reality.")
            st.caption(
                f"The public dataset is a crawl, so it contains videos that were discoverable "
                f"enough to be crawled — a filtered view of what small channels publish. A new "
                f"channel posting into the void is not in it. **This is the product's own thesis "
                f"applied to its own grounding**: banding by size does not remove survivorship "
                f"bias, because the population inside the band is filtered too. Forecasts are "
                f"corrected against observed history rather than presented raw — on "
                f"{gap['observed_videos']} videos, which is thin, and gets less thin as more "
                f"history accumulates."
            )

        # The scoreboard. Every forecast is written down before publication,
        # so this is where it gets marked. The counts that are *not* graded
        # are shown next to the ones that are: a hit rate computed over two
        # of thirteen videos is not a track record, and hiding the
        # denominator would be the flattering version of this panel.
        score, score_df = _cached_scoreboard(band)
        if score["total"]:
            st.markdown("<div class='workflow-header'>Forecast scoreboard"
                        "<span class='eyebrow'>predictions made before publishing, "
                        "graded against what happened</span></div>",
                        unsafe_allow_html=True)
            s1, s2, s3, s4 = st.columns(4)
            with s1:
                st.metric("Gradeable", f"{score['graded']} of {score['total']}",
                          help="Published videos old enough to judge, that had a "
                               "forecast recorded, and that still exist.")
            with s2:
                st.metric(
                    "Landed in band",
                    f"{score['in_band_pct']:.0f}%" if score["in_band_pct"] is not None else "—",
                    help="Share of graded forecasts whose actual reach fell inside "
                         "their own p10-p90 range, scaled to the clip's age.")
            with s3:
                st.metric(
                    "Median actual ÷ predicted",
                    f"{score['median_ratio']:.2f}×" if score["median_ratio"] is not None else "—",
                    help="1.00 would be a perfectly centred forecast. Below 1 means "
                         "the forecast runs high.")
            with s4:
                st.metric("Awaiting maturity", score["pending"],
                          help="Published with a forecast, but too young to judge. A "
                               "lifetime forecast cannot be scored against a clip that "
                               "has not had a lifetime yet.")

            if not score["enough_to_judge"]:
                st.caption(
                    f"**Not enough graded forecasts to report an accuracy figure yet.** "
                    f"{score['graded']} of {score['total']} published videos are gradeable: "
                    f"{score['pending']} are still too young, {score['no_forecast']} were "
                    f"published before forecasts were recorded, and {score['unavailable']} "
                    f"point at videos that no longer exist. This panel is deliberately "
                    f"empty rather than showing a percentage derived from one or two clips."
                )

            if not score_df.empty:
                show = score_df[["clip_id", "status", "age_days", "forecast_p50",
                                 "actual_views", "hook_type"]].copy()
                st.dataframe(
                    show, width='stretch', hide_index=True,
                    column_config={
                        "clip_id": st.column_config.TextColumn("Clip"),
                        "status": st.column_config.TextColumn(
                            "Status", help="graded · pending · no_forecast · unavailable"),
                        "age_days": st.column_config.NumberColumn("Age (days)", format="%d"),
                        "forecast_p50": st.column_config.NumberColumn(
                            "Forecast p50", format="%d"),
                        "actual_views": st.column_config.NumberColumn("Actual", format="%d"),
                        "hook_type": st.column_config.TextColumn("Hook"),
                    },
                )

        facts = _cached_global_facts()
        reach = gb.expected_reach(int(subs), facts=facts)
        lift = gb.subtitle_lift(band, facts=facts)
        days = gb.best_upload_days(size_band=band, facts=facts)

        m1, m2, m3 = st.columns(3)
        with m1:
            st.metric(f"Dataset median · {band} subs",
                      f"{reach['median_views']:,.0f} views" if reach else "—",
                      help="What a typical video from a channel this size got, in the public "
                           "dataset. This is the population figure, not a prediction for your "
                           "channel — see the benchmark-vs-reality panel above, which measures "
                           "how far it sits from what real channels of this size get.")
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
                    template="plotly_dark", color_discrete_sequence=CHART_COLORS, log_y=True,
                    title="Median reach by channel size (4.56B-row dataset)",
                    labels={"bucket": "Subscribers", "median_views": "Median views (log)"},
                )
                fig.add_vline(x=order.index(band), line_dash="dash", line_color="#00E5FF")
                st.plotly_chart(styled(fig), width='stretch')

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
                    template="plotly_dark", color_discrete_sequence=CHART_COLORS,
                    title=f"Median views by upload day · {band} subscribers",
                    labels={"day": "", "median_views": "Median views"},
                )
                st.plotly_chart(styled(fig), width='stretch')
                st.caption(
                    "Within a size band the upload day barely matters — the large "
                    "weekend effect visible across all of YouTube is a channel-size "
                    "artifact, since weekend uploads skew toward small channels."
                )

        hooks = gb.hook_benchmarks(band, facts=facts)
    if not hooks.empty:
        st.markdown("<div class='eyebrow'>Hook patterns · real English titles · "
                    f"{band} subscribers</div>", unsafe_allow_html=True)
        best = gb.best_hook(band, facts=facts)
        if best:
            lift = f"{best['lift_pct']:+.0f}% against an unstyled title, " if best["lift_pct"] else ""
            st.caption(
                f"**{_humanize_hook_type(best['hook'])}** is the best well-sampled hook at "
                f"this channel size — {best['median_views']:,.0f} median views, {lift}from "
                f"{best['sample_videos']:,} real videos. Compare with the seeded "
                f"`video_hook_retention` ranking above: where they disagree, this is the "
                f"one measured on actual outcomes."
            )
        hooks = hooks.assign(bucket_label=hooks["bucket"].map(_humanize_hook_type))
        fig = px.bar(
            hooks.sort_values("median_views"), x="median_views", y="bucket_label",
            orientation="h", template="plotly_dark", color_discrete_sequence=CHART_COLORS, hover_data=["sample_videos"],
            title=f"Median views by title hook pattern · {band} subscribers",
            labels={"median_views": "Median views", "bucket_label": ""},
        )
        st.plotly_chart(styled(fig), width='stretch')
        st.caption(
            "Sampled across 14 uploader ranges rather than one contiguous block, and "
            "restricted to English-language titles — an unfiltered sample is mostly "
            "non-English, which an English pattern matcher silently files as “plain”. "
            f"Buckets under {gb.HOOK_MIN_SAMPLE:,} videos are charted but never headlined — "
            "a thin bucket tops the ranking on noise. Hover for n."
        )

    # --- current trending layer ---
        summary = _cached_trending_summary()
        if summary:
            st.markdown(f"**{_material_icon('trending_up')} Trending right now** — "
                        f"YouTube Data API, snapshot {summary['snapshot_at']}",
                        unsafe_allow_html=True)
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
                st.dataframe(
                    tags, width='stretch', hide_index=True,
                    column_config={
                        "tag": st.column_config.TextColumn("Tag"),
                        "videos": st.column_config.NumberColumn(
                            "Videos", format="%d",
                            help="Trending videos in the snapshot carrying this tag."),
                        "median_views": st.column_config.NumberColumn(
                            "Median views", format="%d"),
                    },
                )
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

        st.markdown(f"<div class='workflow-header'>{_material_icon('movie')} "
                    "Visual Treatment Performance</div>", unsafe_allow_html=True)
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
                        styled(px.bar(
                            _df, x=_dim,
                            y=["avg_3s_retention", "avg_completion_rate", "avg_virality_score"],
                            barmode="group", template="plotly_dark",
                            color_discrete_sequence=CHART_COLORS,
                            labels={"value": "Score / Percent", _dim: _label, "variable": "Metric"},
                        )),
                        width='stretch',
                    )
                else:
                    st.caption(
                        f"{_label} data is all '{_df.iloc[0][_dim]}' so far — generate clips across "
                        f"different hook types to build a comparison."
                    )
    else:
        st.info("No benchmark data available yet.")

    st.markdown(f"<div class='workflow-header'>{_material_icon('target')} "
                "Predicted vs. Actual (YouTube Cross-Validation)</div>", unsafe_allow_html=True)

    outcomes_df = _cached_published_outcomes(urdr)
    if outcomes_df.empty:
        st.info("Publish a short from Tab 1 to start collecting real outcomes here.")
    else:
        if demo_locked("Sync Actual Performance", 'Publishes to a real YouTube channel — disabled on the public demo.', "sync_locked", icon=":material/sync:"):
            pass
        elif st.button("Sync Actual Performance", icon=":material/sync:"):
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
        # A row whose video is deleted, private, or was never published
        # cannot be measured. Plotting it as zero actual views would put a
        # fabricated miss on the chart and drag the apparent accuracy down
        # with data that never existed — which is exactly how a stale
        # 900,000-view row came to dominate this panel.
        unavailable_mask = (outcomes_df["video_unavailable"].astype(bool)
                            if "video_unavailable" in outcomes_df.columns
                            else pd.Series(False, index=outcomes_df.index))
        measurable_df = outcomes_df[~unavailable_mask]
        n_unavailable = int(unavailable_mask.sum())
        if n_unavailable:
            st.caption(
                f"⚠️ {n_unavailable} row(s) point at videos that are deleted, private, or were "
                f"never published. They're kept for the audit trail but excluded from the charts "
                f"below, since an unmeasurable clip isn't a missed prediction."
            )

        # Age context, shown whether or not a forecast exists. Without it a
        # reader sees "3 views" and concludes the system failed, when the
        # clip is hours old and a video from a channel this size reaches
        # only ~70% of its lifetime views after a week or two.
        if not measurable_df.empty and "published_at" in measurable_df.columns:
            ages = (pd.Timestamp.utcnow().tz_localize(None)
                    - pd.to_datetime(measurable_df["published_at"])).dt.total_seconds() / 86400
            fresh = int(sum(gb.too_early_to_judge(float(a), band, facts) for a in ages))
            if fresh:
                st.caption(
                    f"⏳ {fresh} of {len(measurable_df)} published clip(s) are younger than the "
                    f"youngest age bucket the growth curve measures (median age "
                    f"{ages.median():.1f} days). Their view counts are real but not yet "
                    f"comparable to a lifetime median — reach accrues over months."
                )

        synced_df = measurable_df[measurable_df["actual_view_count"] > 0]
        if not synced_df.empty:
            scatter_fig = px.scatter(
                synced_df, x="predicted_virality_score", y="actual_view_count",
                color=synced_df["hook_type"].map(_humanize_hook_type).rename("Hook type"),
                size="actual_view_count", hover_data=["clip_id"],
                template="plotly_dark", color_discrete_sequence=CHART_COLORS, title="Predicted Virality vs. Actual Views",
                labels={"predicted_virality_score": "Predicted Virality Score", "actual_view_count": "Actual Views"},
            )
            st.plotly_chart(styled(scatter_fig), width='stretch')
        else:
            st.caption("Sync actual performance above once your published clips have real view counts to compare against.")

        # Forecast vs. actual. This is the comparison that can actually be
        # right or wrong: predicted_virality_score is a 0-100 internal
        # ranking, so plotting it against view counts only ever shows
        # whether the ordering held. The forecast is in views, so it can be
        # checked against the diagonal.
        forecast_df = measurable_df[
            (measurable_df.get("forecast_views_p50", pd.Series(dtype=float)) > 0)
            & (measurable_df["actual_view_count"] > 0)
        ] if "forecast_views_p50" in measurable_df.columns else pd.DataFrame()

        # A clip younger than anything the growth curve measured cannot be
        # scored against a lifetime forecast. It is not underperforming;
        # there is simply nothing to compare it with. Plotting it anyway is
        # what made hours-old clips read as catastrophic misses.
        if not forecast_df.empty and "published_at" in forecast_df.columns:
            ages = (pd.Timestamp.utcnow().tz_localize(None)
                    - pd.to_datetime(forecast_df["published_at"])).dt.total_seconds() / 86400
            too_young = ages.apply(
                lambda a: gb.too_early_to_judge(float(a), band, facts))
            n_young = int(too_young.sum())
            forecast_df = forecast_df[~too_young]
            if n_young:
                st.caption(
                    f"⏳ {n_young} clip(s) are younger than the youngest age bucket the growth "
                    f"curve measures, so they are held out of the chart below rather than "
                    f"scored as misses. Reach accrues over months: a {band}-subscriber video "
                    f"reaches roughly 70% of its lifetime views only after a week or two."
                )

        if not forecast_df.empty:
            fig = px.scatter(
                forecast_df, x="forecast_views_p50", y="actual_view_count",
                color=forecast_df["hook_type"].map(_humanize_hook_type).rename("Hook type"),
                hover_data=["clip_id", "forecast_views_p90"],
                template="plotly_dark", color_discrete_sequence=CHART_COLORS, log_x=True, log_y=True,
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
            st.plotly_chart(styled(fig), width='stretch')
            st.caption(
                "Points on the dashed line landed exactly where the global data said "
                "comparable videos land. Above it, the clip beat its cohort."
            )
        elif "forecast_views_p50" in outcomes_df.columns:
            st.caption(
                "No clip has both a stored forecast and real views yet — forecasts are "
                "recorded from the next publish onward."
            )

        # One row per published video, newest first. A clip id can legitimately
        # appear several times: clip_001 was uploaded seven times during early
        # testing, and each upload is its own video with its own outcome. The
        # dedup is per video, not per clip.
        display_df = outcomes_df.copy()
        if "video_unavailable" in display_df.columns:
            display_df["measurable"] = ~display_df["video_unavailable"].astype(bool)
        st.dataframe(
            display_df[[
                c for c in [
                    "clip_id", "hook_type", "measurable", "predicted_virality_score",
                    "forecast_views_p50", "actual_view_count", "actual_like_count",
                    "actual_comment_count", "youtube_url", "last_synced_at",
                ] if c in display_df.columns
            ]],
            width='stretch',
            hide_index=True,
            column_config={
                "clip_id": st.column_config.TextColumn("Clip", width="medium"),
                "hook_type": st.column_config.TextColumn("Hook"),
                "measurable": st.column_config.CheckboxColumn(
                    "Live", help="Unticked means the video is deleted, private, or was "
                                 "never published, so it cannot be measured and is "
                                 "excluded from the charts above."),
                "predicted_virality_score": st.column_config.NumberColumn(
                    "Score", format="%.1f",
                    help="Verðandi's internal 0-100 ranking. Relative, not predictive."),
                "forecast_views_p50": st.column_config.NumberColumn(
                    "Forecast", format="%d",
                    help="Grounded p50 reach, recorded before publishing."),
                "actual_view_count": st.column_config.NumberColumn("Views", format="%d"),
                "actual_like_count": st.column_config.NumberColumn("Likes", format="%d"),
                "actual_comment_count": st.column_config.NumberColumn("Comments", format="%d"),
                "youtube_url": st.column_config.LinkColumn("Video", display_text="watch"),
                "last_synced_at": st.column_config.DatetimeColumn(
                    "Synced", format="YYYY-MM-DD HH:mm"),
            },
        )
        st.caption(
            "A clip id can appear more than once — each row is a separate upload with its "
            "own outcome, and early testing published some clips repeatedly."
        )

    if DEMO_MODE:
        st.caption(
            "🔒 The SQL console is disabled on the public demo. It runs user-supplied SQL "
            "against the shared warehouse with write access enabled, which is not something "
            "to expose on an unauthenticated URL."
        )
    else:
        with st.expander("SQL Query Console", icon=":material/terminal:"):
            default_query = (
                "SELECT hook_type, avg(virality_score) AS avg_virality\n"
                "FROM video_hook_retention\n"
                "GROUP BY hook_type\n"
                "ORDER BY avg_virality DESC"
            )
            user_query = st.text_area("Run a custom ClickHouse query:", value=default_query, height=90)
            if st.button("Execute Query", icon=":material/play_arrow:"):
                try:
                    result_df = urdr.execute_custom_query(user_query)
                    st.dataframe(result_df, width='stretch')
                except Exception as e:
                    st.error(f"Query failed: {e}")

# =========================================================================
# NAVIGATION
# =========================================================================
# st.navigation, not st.tabs. Streamlit executes every tab body on every
# rerun regardless of which is visible, so tabs hid these pages while still
# paying for all their ClickHouse round-trips — that was the real cause of
# the slow analytics view. Pages execute only when selected, and they get
# real URLs and back-button behaviour for free.
_PAGE_HOME = st.Page(page_home, title="Home", icon=":material/explore:", default=True)
_PAGE_CREATE = st.Page(page_create, title="Create", icon=":material/content_cut:")
_PAGE_REVIEW = st.Page(page_review, title="Review", icon=":material/balance:")
_PAGE_INTELLIGENCE = st.Page(page_intelligence, title="Intelligence", icon=":material/satellite_alt:")

st.navigation([_PAGE_HOME, _PAGE_CREATE, _PAGE_REVIEW, _PAGE_INTELLIGENCE]).run()
