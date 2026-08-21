"""
Urðr Analytics Tool (ᚢ - Urðr / The Past)
Part of NornPulse: Autonomous Media Engine by Norn Labs (nornlabs.ai)

Urðr weaves the thread of the past. This module queries ClickHouse for historical
video hook retention metrics, drop-off curves, and virality benchmarks to ground
AI clip decisions in empirical audience behavioral data. It also logs new generation
telemetry to close the autonomous feedback loop.

All ClickHouse access goes through the official ClickHouse MCP server
(mcp-clickhouse), via agent/clickhouse_mcp_client.py, per the Agentic Cinema
ClickHouse track requirement.
"""

import logging
import datetime
import math
from typing import Dict, Any, List, Optional
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

import agent.clickhouse_mcp_client as ch  # noqa: E402 – imported after load_dotenv intentionally

logger = logging.getLogger("nornpulse.urdr")


# Default synthetic seed dataset for ClickHouse initialization & fallback
DEFAULT_HOOK_BENCHMARKS = [
    {
        "video_id": "vid_2026_001",
        "hook_type": "contrarian_claim",
        "hook_text": "Stop using traditional databases for real-time analytics.",
        "duration_sec": 38.5,
        "avg_3s_retention_pct": 88.4,
        "avg_15s_retention_pct": 74.2,
        "avg_30s_retention_pct": 61.8,
        "completion_rate_pct": 52.3,
        "virality_score": 91.2,
        "topic_category": "tech_ai",
        "sample_size_views": 320000,
    },
    {
        "video_id": "vid_2026_002",
        "hook_type": "curiosity_gap",
        "hook_text": "The hidden parameter in Gemini 2.0 that changes everything.",
        "duration_sec": 44.0,
        "avg_3s_retention_pct": 92.1,
        "avg_15s_retention_pct": 81.5,
        "avg_30s_retention_pct": 68.3,
        "completion_rate_pct": 58.7,
        "virality_score": 94.8,
        "topic_category": "tech_ai",
        "sample_size_views": 540000,
    },
    {
        "video_id": "vid_2026_003",
        "hook_type": "shock_stat",
        "hook_text": "93% of AI startups will go bankrupt in the next 18 months.",
        "duration_sec": 29.0,
        "avg_3s_retention_pct": 94.6,
        "avg_15s_retention_pct": 83.1,
        "avg_30s_retention_pct": 69.5,
        "completion_rate_pct": 64.1,
        "virality_score": 97.4,
        "topic_category": "business_tech",
        "sample_size_views": 890000,
    },
    {
        "video_id": "vid_2026_004",
        "hook_type": "problem_agitation",
        "hook_text": "You are wasting 4 hours every week manually editing video shorts.",
        "duration_sec": 35.2,
        "avg_3s_retention_pct": 85.3,
        "avg_15s_retention_pct": 71.0,
        "avg_30s_retention_pct": 56.4,
        "completion_rate_pct": 48.9,
        "virality_score": 86.5,
        "topic_category": "creator_tools",
        "sample_size_views": 210000,
    },
    {
        "video_id": "vid_2026_005",
        "hook_type": "story_in_medias_res",
        "hook_text": "At 3 AM yesterday, our entire autonomous agent cluster woke up.",
        "duration_sec": 52.0,
        "avg_3s_retention_pct": 89.7,
        "avg_15s_retention_pct": 79.4,
        "avg_30s_retention_pct": 66.2,
        "completion_rate_pct": 55.0,
        "virality_score": 93.1,
        "topic_category": "storytelling_ai",
        "sample_size_views": 610000,
    },
    {
        "video_id": "vid_2026_006",
        "hook_type": "visual_disruption",
        "hook_text": "Look at what happens when we benchmark 1M queries per second.",
        "duration_sec": 24.5,
        "avg_3s_retention_pct": 91.0,
        "avg_15s_retention_pct": 78.8,
        "avg_30s_retention_pct": 62.0,
        "completion_rate_pct": 59.8,
        "virality_score": 89.9,
        "topic_category": "engineering",
        "sample_size_views": 390000,
    },
    {
        "video_id": "vid_2026_007",
        "hook_type": "direct_question",
        "hook_text": "Why is everyone suddenly talking about Norn Labs?",
        "duration_sec": 31.0,
        "avg_3s_retention_pct": 81.2,
        "avg_15s_retention_pct": 66.5,
        "avg_30s_retention_pct": 51.3,
        "completion_rate_pct": 44.2,
        "virality_score": 79.5,
        "topic_category": "branding",
        "sample_size_views": 150000,
    },
    {
        "video_id": "vid_2026_008",
        "hook_type": "metaphor_analogy",
        "hook_text": "Think of ClickHouse like an F1 engine strapped to your database.",
        "duration_sec": 41.5,
        "avg_3s_retention_pct": 87.5,
        "avg_15s_retention_pct": 75.3,
        "avg_30s_retention_pct": 63.8,
        "completion_rate_pct": 53.6,
        "virality_score": 88.7,
        "topic_category": "data_infra",
        "sample_size_views": 275000,
    },
    {
        "video_id": "vid_2026_009",
        "hook_type": "curiosity_gap",
        "hook_text": "Never publish a vertical video before applying this rule.",
        "duration_sec": 28.0,
        "avg_3s_retention_pct": 93.8,
        "avg_15s_retention_pct": 84.6,
        "avg_30s_retention_pct": 71.2,
        "completion_rate_pct": 66.5,
        "virality_score": 96.2,
        "topic_category": "growth_hacks",
        "sample_size_views": 780000,
    },
    {
        "video_id": "vid_2026_010",
        "hook_type": "contrarian_claim",
        "hook_text": "Long-form content is not dead; your editing is just boring.",
        "duration_sec": 36.8,
        "avg_3s_retention_pct": 89.2,
        "avg_15s_retention_pct": 76.9,
        "avg_30s_retention_pct": 64.0,
        "completion_rate_pct": 54.1,
        "virality_score": 90.5,
        "topic_category": "media_strategy",
        "sample_size_views": 410000,
    },
]


# Default synthetic seed dataset correlating musical attributes with global
# YouTube Shorts virality, per hook_type — grounds Bragi's Lyria prompts
# the same way DEFAULT_HOOK_BENCHMARKS grounds Verðandi's hook_type choice.
# Two rows per hook_type (a stronger and a weaker performer) so the
# top-scoring lookup has a real choice to make, not just one option.
DEFAULT_MUSIC_BENCHMARKS = [
    {"hook_type": "shock_stat", "genre": "trap-hybrid trailer", "mood": "aggressive", "bpm": 140, "energy_level": 0.9, "avg_virality_score": 96.7, "sample_size_views": 780000, "topic_category": "business_tech"},
    {"hook_type": "shock_stat", "genre": "orchestral hit stabs", "mood": "dramatic", "bpm": 120, "energy_level": 0.85, "avg_virality_score": 90.3, "sample_size_views": 300000, "topic_category": "business_tech"},
    {"hook_type": "curiosity_gap", "genre": "synthwave", "mood": "mysterious", "bpm": 110, "energy_level": 0.7, "avg_virality_score": 93.8, "sample_size_views": 610000, "topic_category": "tech_ai"},
    {"hook_type": "curiosity_gap", "genre": "ambient electronic", "mood": "curious", "bpm": 90, "energy_level": 0.5, "avg_virality_score": 85.1, "sample_size_views": 200000, "topic_category": "growth_hacks"},
    {"hook_type": "contrarian_claim", "genre": "cinematic trailer", "mood": "tense", "bpm": 100, "energy_level": 0.75, "avg_virality_score": 89.5, "sample_size_views": 210000, "topic_category": "media_strategy"},
    {"hook_type": "contrarian_claim", "genre": "lo-fi hip hop", "mood": "moody", "bpm": 80, "energy_level": 0.4, "avg_virality_score": 78.2, "sample_size_views": 90000, "topic_category": "media_strategy"},
    {"hook_type": "problem_agitation", "genre": "dark ambient", "mood": "anxious", "bpm": 85, "energy_level": 0.55, "avg_virality_score": 82.4, "sample_size_views": 150000, "topic_category": "creator_tools"},
    {"hook_type": "problem_agitation", "genre": "minimal piano", "mood": "melancholic", "bpm": 70, "energy_level": 0.3, "avg_virality_score": 74.0, "sample_size_views": 80000, "topic_category": "creator_tools"},
    {"hook_type": "story_in_medias_res", "genre": "cinematic orchestral", "mood": "epic", "bpm": 100, "energy_level": 0.8, "avg_virality_score": 91.5, "sample_size_views": 400000, "topic_category": "storytelling_ai"},
    {"hook_type": "story_in_medias_res", "genre": "acoustic guitar", "mood": "intimate", "bpm": 75, "energy_level": 0.35, "avg_virality_score": 79.9, "sample_size_views": 120000, "topic_category": "storytelling_ai"},
    {"hook_type": "visual_disruption", "genre": "glitch electronic", "mood": "chaotic", "bpm": 150, "energy_level": 0.95, "avg_virality_score": 88.6, "sample_size_views": 350000, "topic_category": "engineering"},
    {"hook_type": "visual_disruption", "genre": "industrial techno", "mood": "intense", "bpm": 130, "energy_level": 0.9, "avg_virality_score": 84.2, "sample_size_views": 180000, "topic_category": "engineering"},
    {"hook_type": "direct_question", "genre": "upbeat pop instrumental", "mood": "playful", "bpm": 115, "energy_level": 0.65, "avg_virality_score": 76.3, "sample_size_views": 100000, "topic_category": "branding"},
    {"hook_type": "direct_question", "genre": "chill hip hop", "mood": "casual", "bpm": 90, "energy_level": 0.45, "avg_virality_score": 70.1, "sample_size_views": 60000, "topic_category": "branding"},
    {"hook_type": "metaphor_analogy", "genre": "cinematic ambient", "mood": "thoughtful", "bpm": 95, "energy_level": 0.6, "avg_virality_score": 85.8, "sample_size_views": 250000, "topic_category": "data_infra"},
    {"hook_type": "metaphor_analogy", "genre": "jazz fusion", "mood": "smooth", "bpm": 100, "energy_level": 0.55, "avg_virality_score": 80.2, "sample_size_views": 130000, "topic_category": "data_infra"},
]


# Default synthetic seed dataset correlating crop framing, camera motion,
# and color grade with global YouTube Shorts virality, per hook_type --
# grounds Skuld's visual treatment choice the same way DEFAULT_MUSIC_BENCHMARKS
# grounds Bragi's Lyria prompt. Two rows per hook_type (a stronger and a
# weaker performer), same rationale as the music benchmarks: a real choice
# for the top-scoring lookup to make, not just one option.
#
# crop_mode: center_crop | blurred_background | top_anchored_crop | cinematic_letterbox
# motion_effect: none | ken_burns_zoom | punch_in_zoom | shake
# color_grade: neutral | cool_desaturated | warm_glow | vibrant_punch
DEFAULT_VISUAL_BENCHMARKS = [
    {"hook_type": "shock_stat", "crop_mode": "center_crop", "motion_effect": "punch_in_zoom", "color_grade": "vibrant_punch", "avg_virality_score": 94.8, "sample_size_views": 690000, "topic_category": "business_tech"},
    {"hook_type": "shock_stat", "crop_mode": "cinematic_letterbox", "motion_effect": "shake", "color_grade": "cool_desaturated", "avg_virality_score": 87.1, "sample_size_views": 260000, "topic_category": "business_tech"},
    {"hook_type": "curiosity_gap", "crop_mode": "blurred_background", "motion_effect": "ken_burns_zoom", "color_grade": "cool_desaturated", "avg_virality_score": 91.2, "sample_size_views": 540000, "topic_category": "tech_ai"},
    {"hook_type": "curiosity_gap", "crop_mode": "center_crop", "motion_effect": "none", "color_grade": "neutral", "avg_virality_score": 82.4, "sample_size_views": 180000, "topic_category": "growth_hacks"},
    {"hook_type": "contrarian_claim", "crop_mode": "cinematic_letterbox", "motion_effect": "shake", "color_grade": "cool_desaturated", "avg_virality_score": 88.9, "sample_size_views": 200000, "topic_category": "media_strategy"},
    {"hook_type": "contrarian_claim", "crop_mode": "center_crop", "motion_effect": "punch_in_zoom", "color_grade": "vibrant_punch", "avg_virality_score": 76.5, "sample_size_views": 85000, "topic_category": "media_strategy"},
    {"hook_type": "problem_agitation", "crop_mode": "cinematic_letterbox", "motion_effect": "shake", "color_grade": "cool_desaturated", "avg_virality_score": 80.7, "sample_size_views": 140000, "topic_category": "creator_tools"},
    {"hook_type": "problem_agitation", "crop_mode": "blurred_background", "motion_effect": "ken_burns_zoom", "color_grade": "neutral", "avg_virality_score": 72.3, "sample_size_views": 75000, "topic_category": "creator_tools"},
    {"hook_type": "story_in_medias_res", "crop_mode": "blurred_background", "motion_effect": "ken_burns_zoom", "color_grade": "warm_glow", "avg_virality_score": 90.1, "sample_size_views": 380000, "topic_category": "storytelling_ai"},
    {"hook_type": "story_in_medias_res", "crop_mode": "cinematic_letterbox", "motion_effect": "none", "color_grade": "neutral", "avg_virality_score": 77.8, "sample_size_views": 110000, "topic_category": "storytelling_ai"},
    {"hook_type": "visual_disruption", "crop_mode": "center_crop", "motion_effect": "shake", "color_grade": "vibrant_punch", "avg_virality_score": 87.0, "sample_size_views": 330000, "topic_category": "engineering"},
    {"hook_type": "visual_disruption", "crop_mode": "cinematic_letterbox", "motion_effect": "punch_in_zoom", "color_grade": "cool_desaturated", "avg_virality_score": 81.6, "sample_size_views": 170000, "topic_category": "engineering"},
    {"hook_type": "direct_question", "crop_mode": "top_anchored_crop", "motion_effect": "ken_burns_zoom", "color_grade": "warm_glow", "avg_virality_score": 75.0, "sample_size_views": 95000, "topic_category": "branding"},
    {"hook_type": "direct_question", "crop_mode": "center_crop", "motion_effect": "none", "color_grade": "neutral", "avg_virality_score": 68.9, "sample_size_views": 55000, "topic_category": "branding"},
    {"hook_type": "metaphor_analogy", "crop_mode": "blurred_background", "motion_effect": "ken_burns_zoom", "color_grade": "warm_glow", "avg_virality_score": 84.2, "sample_size_views": 230000, "topic_category": "data_infra"},
    {"hook_type": "metaphor_analogy", "crop_mode": "cinematic_letterbox", "motion_effect": "none", "color_grade": "cool_desaturated", "avg_virality_score": 78.6, "sample_size_views": 125000, "topic_category": "data_infra"},
]


def _compute_actual_virality_score(view_count: int, like_count: int, comment_count: int) -> float:
    """
    Heuristic 0-100 virality proxy from real YouTube stats, scaled to sit
    in roughly the same range as the synthetic seed benchmarks. There's no
    single industry-standard "virality score", so this is a documented
    heuristic, not a validated metric: a log-scaled view-count component
    (rewards reach, with diminishing returns — 30 at ~1K views, 50 at
    ~100K, capping at 60 around 1M+) blended with an engagement-rate
    component (likes + 3x comments, since a comment is rarer/costs more
    effort than a like, relative to views; capped at 40, reached around a
    ~10% weighted engagement rate). Raw view count alone can't distinguish
    a clip that genuinely resonated from one that was merely shown to a
    lot of people, so both terms matter.
    """
    view_component = min(60.0, 10.0 * math.log10(max(view_count, 0) + 1))
    engagement_rate = (like_count + 3 * comment_count) / max(view_count, 1)
    engagement_component = min(40.0, engagement_rate * 400.0)
    return round(min(100.0, view_component + engagement_component), 2)


class UrdrAnalytics:
    """
    Urðr: Past Video Hook Analytics & ClickHouse Intelligence Tool.

    All queries route through the official ClickHouse MCP server via
    agent/clickhouse_mcp_client.py. Connection details are read by that
    server directly from environment variables (CLICKHOUSE_HOST,
    CLICKHOUSE_USER, CLICKHOUSE_PASSWORD, CLICKHOUSE_SECURE,
    CLICKHOUSE_DATABASE) — set these in .env.
    """

    def __init__(self):
        self._connected = False
        # Human-readable reason the last connect() failed (None when
        # healthy) — surfaced by the UI so a degraded instance announces
        # itself instead of quietly serving fallback data.
        self.connection_error: Optional[str] = None
        self._fallback_df = pd.DataFrame(DEFAULT_HOOK_BENCHMARKS)
        self._fallback_music_df = pd.DataFrame(DEFAULT_MUSIC_BENCHMARKS)
        self._fallback_visual_df = pd.DataFrame(DEFAULT_VISUAL_BENCHMARKS)
        self.connect()

    def connect(self) -> bool:
        """
        Verifies the ClickHouse MCP server can reach ClickHouse, and
        initializes schema. On failure the reason is kept in
        self.connection_error so callers (the UI in particular) can show
        WHY it's degraded rather than only that it is — a silent fallback
        makes a misconfigured deployment look perfectly healthy, which is
        the failure mode this whole path is guarding against.
        """
        import time
        _t0 = time.perf_counter()
        try:
            problem = ch.describe_connection()
            if problem is None:
                self._connected = True
                self.connection_error = None
                logger.info(f"⚡ Urðr successfully connected to ClickHouse via mcp-clickhouse ({time.perf_counter() - _t0:.1f}s — this is a one-time cold-start cost per app process, includes MCP subprocess spawn and any ClickHouse Cloud idle-resume delay).")
                self.init_schema()
                logger.info(f"⏱️ Total connect() + init_schema() took {time.perf_counter() - _t0:.1f}s")
                return True
            self._connected = False
            self.connection_error = problem
            logger.warning(f"⚠️ Urðr ClickHouse MCP unavailable. Operating in resilient fallback mode. Reason: {problem}")
            return False
        except Exception as e:
            self._connected = False
            self.connection_error = str(e)
            logger.warning(f"⚠️ Urðr ClickHouse MCP connection unavailable ({e}). Operating in resilient fallback mode.")
            return False

    def is_connected(self) -> bool:
        """
        Returns whether the last known connection check succeeded. This is
        a cached flag rather than a fresh ping on every call — each MCP
        call spawns a subprocess, so re-pinging on every UI render would
        add real latency. Call connect() again to force a fresh check.
        """
        return self._connected

    def init_schema(self) -> None:
        """Initializes database schema and populates seed dataset if empty."""
        if not self.is_connected():
            return

        try:
            ch.run_query("""
            CREATE TABLE IF NOT EXISTS video_hook_retention (
                video_id String,
                hook_type LowCardinality(String),
                hook_text String,
                duration_sec Float32,
                avg_3s_retention_pct Float32,
                avg_15s_retention_pct Float32,
                avg_30s_retention_pct Float32,
                completion_rate_pct Float32,
                virality_score Float32,
                topic_category LowCardinality(String),
                sample_size_views UInt32,
                created_at DateTime DEFAULT now()
            ) ENGINE = MergeTree()
            ORDER BY (hook_type, virality_score, video_id);
            """)

            count_result = ch.run_query("SELECT count() AS cnt FROM video_hook_retention")
            count = count_result.get("rows", [[0]])[0][0] if count_result.get("rows") else 0
            if count == 0:
                self.seed_benchmarks()

            # Non-destructive: adds the column to any table created before
            # this field existed, without touching existing rows (they get
            # the default value). Safe to run on every startup.
            ch.run_query("""
            ALTER TABLE video_hook_retention
            ADD COLUMN IF NOT EXISTS crop_mode LowCardinality(String) DEFAULT 'unknown'
            """)

            # Cross-validation table: real YouTube outcomes for clips this
            # pipeline actually published, so predictions can be checked
            # against ground truth rather than only synthetic benchmarks.
            ch.run_query("""
            CREATE TABLE IF NOT EXISTS published_clip_outcomes (
                clip_id String,
                youtube_video_id String,
                youtube_url String,
                hook_type LowCardinality(String),
                predicted_virality_score Float32,
                predicted_3s_retention_pct Float32,
                actual_view_count UInt64 DEFAULT 0,
                actual_like_count UInt64 DEFAULT 0,
                actual_comment_count UInt64 DEFAULT 0,
                last_synced_at Nullable(DateTime),
                published_at DateTime DEFAULT now()
            ) ENGINE = MergeTree()
            ORDER BY (published_at, clip_id);
            """)

            # Grounds Bragi's Lyria-composed background scores: correlates
            # musical attributes (genre/mood/bpm/energy) with global YouTube
            # Shorts virality per hook_type, the same way video_hook_retention
            # grounds Verðandi's hook_type choice.
            ch.run_query("""
            CREATE TABLE IF NOT EXISTS music_virality_benchmarks (
                hook_type LowCardinality(String),
                genre LowCardinality(String),
                mood LowCardinality(String),
                bpm UInt16,
                energy_level Float32,
                avg_virality_score Float32,
                sample_size_views UInt32,
                topic_category LowCardinality(String),
                created_at DateTime DEFAULT now()
            ) ENGINE = MergeTree()
            ORDER BY (hook_type, avg_virality_score);
            """)
            music_count_result = ch.run_query("SELECT count() AS cnt FROM music_virality_benchmarks")
            music_count = music_count_result.get("rows", [[0]])[0][0] if music_count_result.get("rows") else 0
            if music_count == 0:
                self.seed_music_benchmarks()

            # Grounds Skuld's crop framing / camera motion / color grade per
            # clip, the same way music_virality_benchmarks grounds Bragi's
            # score -- correlates visual treatment with historical virality
            # per hook_type, so the "sentiment" driving the edit is Urðr's
            # real data rather than an ad hoc per-render model guess.
            ch.run_query("""
            CREATE TABLE IF NOT EXISTS visual_style_benchmarks (
                hook_type LowCardinality(String),
                crop_mode LowCardinality(String),
                motion_effect LowCardinality(String),
                color_grade LowCardinality(String),
                avg_virality_score Float32,
                sample_size_views UInt32,
                topic_category LowCardinality(String),
                created_at DateTime DEFAULT now()
            ) ENGINE = MergeTree()
            ORDER BY (hook_type, avg_virality_score);
            """)
            visual_count_result = ch.run_query("SELECT count() AS cnt FROM visual_style_benchmarks")
            visual_count = visual_count_result.get("rows", [[0]])[0][0] if visual_count_result.get("rows") else 0
            if visual_count == 0:
                self.seed_visual_benchmarks()
        except Exception as e:
            logger.error(f"Error initializing ClickHouse schema: {e}")

    def seed_benchmarks(self) -> int:
        """Seeds standard video hook benchmarks into ClickHouse."""
        if not self.is_connected():
            return len(self._fallback_df)

        try:
            values_clauses = []
            for b in DEFAULT_HOOK_BENCHMARKS:
                values_clauses.append(
                    "(" + ", ".join([
                        ch.sql_literal(b["video_id"]),
                        ch.sql_literal(b["hook_type"]),
                        ch.sql_literal(b["hook_text"]),
                        ch.sql_literal(b["duration_sec"]),
                        ch.sql_literal(b["avg_3s_retention_pct"]),
                        ch.sql_literal(b["avg_15s_retention_pct"]),
                        ch.sql_literal(b["avg_30s_retention_pct"]),
                        ch.sql_literal(b["completion_rate_pct"]),
                        ch.sql_literal(b["virality_score"]),
                        ch.sql_literal(b["topic_category"]),
                        ch.sql_literal(b["sample_size_views"]),
                    ]) + ")"
                )
            query = (
                "INSERT INTO video_hook_retention "
                "(video_id, hook_type, hook_text, duration_sec, avg_3s_retention_pct, "
                "avg_15s_retention_pct, avg_30s_retention_pct, completion_rate_pct, "
                "virality_score, topic_category, sample_size_views) VALUES "
                + ", ".join(values_clauses)
            )
            ch.run_query(query)
            logger.info(f"Seeded {len(DEFAULT_HOOK_BENCHMARKS)} retention benchmark records into ClickHouse.")
            return len(DEFAULT_HOOK_BENCHMARKS)
        except Exception as e:
            logger.error(f"Failed to seed benchmarks into ClickHouse: {e}")
            return 0

    def seed_music_benchmarks(self) -> int:
        """Seeds the hook_type -> musical attribute virality correlations Bragi grounds its Lyria prompts in."""
        if not self.is_connected():
            return len(self._fallback_music_df)

        try:
            values_clauses = []
            for b in DEFAULT_MUSIC_BENCHMARKS:
                values_clauses.append(
                    "(" + ", ".join([
                        ch.sql_literal(b["hook_type"]),
                        ch.sql_literal(b["genre"]),
                        ch.sql_literal(b["mood"]),
                        ch.sql_literal(b["bpm"]),
                        ch.sql_literal(b["energy_level"]),
                        ch.sql_literal(b["avg_virality_score"]),
                        ch.sql_literal(b["sample_size_views"]),
                        ch.sql_literal(b["topic_category"]),
                    ]) + ")"
                )
            query = (
                "INSERT INTO music_virality_benchmarks "
                "(hook_type, genre, mood, bpm, energy_level, avg_virality_score, "
                "sample_size_views, topic_category) VALUES "
                + ", ".join(values_clauses)
            )
            ch.run_query(query)
            logger.info(f"Seeded {len(DEFAULT_MUSIC_BENCHMARKS)} music virality benchmark records into ClickHouse.")
            return len(DEFAULT_MUSIC_BENCHMARKS)
        except Exception as e:
            logger.error(f"Failed to seed music benchmarks into ClickHouse: {e}")
            return 0

    def seed_visual_benchmarks(self) -> int:
        """Seeds the hook_type -> crop/motion/color-grade virality correlations Skuld grounds its visual treatment in."""
        if not self.is_connected():
            return len(self._fallback_visual_df)

        try:
            values_clauses = []
            for b in DEFAULT_VISUAL_BENCHMARKS:
                values_clauses.append(
                    "(" + ", ".join([
                        ch.sql_literal(b["hook_type"]),
                        ch.sql_literal(b["crop_mode"]),
                        ch.sql_literal(b["motion_effect"]),
                        ch.sql_literal(b["color_grade"]),
                        ch.sql_literal(b["avg_virality_score"]),
                        ch.sql_literal(b["sample_size_views"]),
                        ch.sql_literal(b["topic_category"]),
                    ]) + ")"
                )
            query = (
                "INSERT INTO visual_style_benchmarks "
                "(hook_type, crop_mode, motion_effect, color_grade, avg_virality_score, "
                "sample_size_views, topic_category) VALUES "
                + ", ".join(values_clauses)
            )
            ch.run_query(query)
            logger.info(f"Seeded {len(DEFAULT_VISUAL_BENCHMARKS)} visual style benchmark records into ClickHouse.")
            return len(DEFAULT_VISUAL_BENCHMARKS)
        except Exception as e:
            logger.error(f"Failed to seed visual benchmarks into ClickHouse: {e}")
            return 0

    def get_top_visual_benchmark(self, hook_type: str, topic_category: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Returns the single highest-virality visual_style_benchmarks row for
        hook_type -- the ClickHouse-grounded crop_mode/motion_effect/
        color_grade Skuld renders with. Same progressive fallback as
        get_top_music_benchmark (topic-scoped -> hook_type only -> global
        top row), so a clip always gets a concrete, data-grounded visual
        treatment even for a hook_type or topic_category outside the
        seeded taxonomy.
        """
        def _query(where_clauses: List[str]) -> str:
            where_str = " AND ".join(where_clauses) if where_clauses else "1=1"
            return f"""
            SELECT crop_mode, motion_effect, color_grade, avg_virality_score
            FROM visual_style_benchmarks
            WHERE {where_str}
            ORDER BY avg_virality_score DESC
            LIMIT 1
            """

        if self.is_connected():
            try:
                clauses = [f"hook_type = {ch.sql_literal(hook_type)}"]
                if topic_category and topic_category != "all":
                    clauses.append(f"topic_category = {ch.sql_literal(topic_category)}")
                df = ch.run_query_df(_query(clauses))
                if df.empty and topic_category:
                    df = ch.run_query_df(_query([f"hook_type = {ch.sql_literal(hook_type)}"]))
                if df.empty:
                    df = ch.run_query_df(_query([]))
                if not df.empty:
                    return df.iloc[0].to_dict()
            except Exception as e:
                logger.error(f"Visual style benchmark query failed: {e}")

        # In-memory fallback
        fdf = self._fallback_visual_df
        scoped = fdf[fdf["hook_type"] == hook_type]
        if scoped.empty:
            scoped = fdf
        if scoped.empty:
            return None
        return scoped.sort_values("avg_virality_score", ascending=False).iloc[0].to_dict()

    def get_top_music_benchmark(self, hook_type: str, topic_category: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Returns the single highest-virality music_virality_benchmarks row
        for hook_type — the ClickHouse-grounded genre/mood/bpm/energy_level
        Bragi composes its Lyria prompt from. Falls back across
        progressively looser scopes (topic-scoped -> hook_type only ->
        global top row) rather than returning nothing, so Bragi always has
        something concrete grounded in real data to compose from, even for
        a hook_type or topic_category outside the seeded taxonomy.
        """
        def _query(where_clauses: List[str]) -> str:
            where_str = " AND ".join(where_clauses) if where_clauses else "1=1"
            return f"""
            SELECT genre, mood, bpm, energy_level, avg_virality_score
            FROM music_virality_benchmarks
            WHERE {where_str}
            ORDER BY avg_virality_score DESC
            LIMIT 1
            """

        if self.is_connected():
            try:
                clauses = [f"hook_type = {ch.sql_literal(hook_type)}"]
                if topic_category and topic_category != "all":
                    clauses.append(f"topic_category = {ch.sql_literal(topic_category)}")
                df = ch.run_query_df(_query(clauses))
                if df.empty and topic_category:
                    df = ch.run_query_df(_query([f"hook_type = {ch.sql_literal(hook_type)}"]))
                if df.empty:
                    df = ch.run_query_df(_query([]))
                if not df.empty:
                    return df.iloc[0].to_dict()
            except Exception as e:
                logger.error(f"Music benchmark query failed: {e}")

        # In-memory fallback
        fdf = self._fallback_music_df
        scoped = fdf[fdf["hook_type"] == hook_type]
        if scoped.empty:
            scoped = fdf
        if scoped.empty:
            return None
        return scoped.sort_values("avg_virality_score", ascending=False).iloc[0].to_dict()

    def log_generated_clip(
        self,
        clip_id: str,
        hook_type: str,
        hook_text: str,
        duration_sec: float,
        predicted_3s: float,
        predicted_completion: float,
        virality_score: float,
        topic_category: str = "generated_clip",
        crop_mode: str = "unknown",
    ) -> bool:
        """
        Closes the loop by logging newly generated clips and their predicted
        telemetry back into the ClickHouse database.
        """
        if not self.is_connected():
            logger.warning("ClickHouse disconnected; unable to log generated clip.")
            return False

        try:
            query = (
                "INSERT INTO video_hook_retention "
                "(video_id, hook_type, hook_text, duration_sec, avg_3s_retention_pct, "
                "avg_15s_retention_pct, avg_30s_retention_pct, completion_rate_pct, "
                "virality_score, topic_category, sample_size_views, crop_mode) VALUES ("
                + ", ".join([
                    ch.sql_literal(clip_id),
                    ch.sql_literal(hook_type),
                    ch.sql_literal(hook_text),
                    ch.sql_literal(float(duration_sec)),
                    ch.sql_literal(float(predicted_3s)),
                    ch.sql_literal(0.0),  # 15s prediction placeholder
                    ch.sql_literal(0.0),  # 30s prediction placeholder
                    ch.sql_literal(float(predicted_completion)),
                    ch.sql_literal(float(virality_score)),
                    ch.sql_literal(topic_category),
                    ch.sql_literal(0),  # sample_size_views starts at 0
                    ch.sql_literal(crop_mode),
                ]) + ")"
            )
            ch.run_query(query)
            logger.info(f"Logged generated clip {clip_id} prediction telemetry to ClickHouse.")
            return True
        except Exception as e:
            logger.error(f"Failed to log generated clip to ClickHouse: {e}")
            return False

    def log_published_outcome(
        self,
        clip_id: str,
        youtube_video_id: str,
        youtube_url: str,
        hook_type: str,
        predicted_virality_score: float,
        predicted_3s_retention_pct: float,
    ) -> bool:
        """
        Records that a generated clip was actually published to YouTube.
        This is the anchor row `sync_actual_stats` later updates with real
        performance, closing the loop from prediction to ground truth.
        """
        if not self.is_connected():
            logger.warning("ClickHouse disconnected; unable to log published outcome.")
            return False

        try:
            query = (
                "INSERT INTO published_clip_outcomes "
                "(clip_id, youtube_video_id, youtube_url, hook_type, "
                "predicted_virality_score, predicted_3s_retention_pct, "
                "actual_view_count, actual_like_count, actual_comment_count, last_synced_at) VALUES ("
                + ", ".join([
                    ch.sql_literal(clip_id),
                    ch.sql_literal(youtube_video_id),
                    ch.sql_literal(youtube_url),
                    ch.sql_literal(hook_type),
                    ch.sql_literal(float(predicted_virality_score)),
                    ch.sql_literal(float(predicted_3s_retention_pct)),
                    ch.sql_literal(0), ch.sql_literal(0), ch.sql_literal(0),
                    ch.sql_literal(None),
                ]) + ")"
            )
            ch.run_query(query)
            logger.info(f"Logged published outcome for {clip_id} -> {youtube_video_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to log published outcome: {e}")
            return False

    def sync_actual_stats(
        self, youtube_video_id: str, view_count: int, like_count: int, comment_count: int
    ) -> bool:
        """
        Writes real YouTube performance numbers back onto a previously
        logged published_clip_outcomes row. ClickHouse's MergeTree doesn't
        do cheap in-place UPDATEs, so this appends a fresh row for the same
        clip_id/youtube_video_id with the latest stats and a new
        last_synced_at — reads always take the most recent row per video
        (see get_published_outcomes).
        """
        if not self.is_connected():
            logger.warning("ClickHouse disconnected; unable to sync actual stats.")
            return False

        try:
            existing = ch.run_query_df(f"""
                SELECT clip_id, youtube_url, hook_type,
                       predicted_virality_score, predicted_3s_retention_pct
                FROM published_clip_outcomes
                WHERE youtube_video_id = {ch.sql_literal(youtube_video_id)}
                ORDER BY published_at DESC
                LIMIT 1
            """)
            if existing.empty:
                logger.warning(f"No published_clip_outcomes row found for {youtube_video_id}; cannot sync.")
                return False

            row = existing.iloc[0]
            query = (
                "INSERT INTO published_clip_outcomes "
                "(clip_id, youtube_video_id, youtube_url, hook_type, "
                "predicted_virality_score, predicted_3s_retention_pct, "
                "actual_view_count, actual_like_count, actual_comment_count, last_synced_at) VALUES ("
                + ", ".join([
                    ch.sql_literal(row["clip_id"]),
                    ch.sql_literal(youtube_video_id),
                    ch.sql_literal(row["youtube_url"]),
                    ch.sql_literal(row["hook_type"]),
                    ch.sql_literal(float(row["predicted_virality_score"])),
                    ch.sql_literal(float(row["predicted_3s_retention_pct"])),
                    ch.sql_literal(int(view_count)),
                    ch.sql_literal(int(like_count)),
                    ch.sql_literal(int(comment_count)),
                    ch.sql_literal(datetime.datetime.utcnow()),
                ]) + ")"
            )
            ch.run_query(query)
            logger.info(f"Synced actual stats for {youtube_video_id}: {view_count} views.")

            # Close the loop: feed this real outcome back into
            # video_hook_retention so future hook_type grounding reflects
            # what actually performed, not just synthetic seed data and
            # the prediction made at generation time. A failure here
            # doesn't fail the sync itself — the cross-validation display
            # already has what it needs from the insert above.
            self.log_actual_outcome_to_benchmarks(
                clip_id=row["clip_id"], hook_type=row["hook_type"],
                view_count=view_count, like_count=like_count, comment_count=comment_count,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to sync actual stats: {e}")
            return False

    def log_actual_outcome_to_benchmarks(
        self, clip_id: str, hook_type: str, view_count: int, like_count: int, comment_count: int,
    ) -> bool:
        """
        Converts a clip's real published performance into a genuine data
        point in video_hook_retention (see _compute_actual_virality_score
        for the scoring heuristic), tagged topic_category='actual_outcome'
        and video_id=f"actual_{clip_id}" so it's distinguishable from
        synthetic seed rows and the predicted-at-generation-time row for
        the same clip. get_hook_type_benchmarks' AVG(virality_score)
        GROUP BY hook_type then blends real outcomes in over time —
        hook types that actually perform well accumulate real high-
        virality samples, genuinely shifting what Verðandi is grounded in
        rather than the benchmarks staying static forever.

        Idempotent per clip: only the first successful call for a given
        clip_id inserts a row, so re-syncing the same video's stats
        repeatedly (each sync appends a fresh published_clip_outcomes row
        by design) doesn't multiply that one clip's weight in the
        aggregate — later syncs still update the cross-validation display,
        they just don't re-feed the benchmark.
        """
        if not self.is_connected():
            return False

        actual_video_id = f"actual_{clip_id}"
        try:
            already_logged = ch.run_query_df(
                f"SELECT video_id FROM video_hook_retention WHERE video_id = {ch.sql_literal(actual_video_id)} LIMIT 1"
            )
            if not already_logged.empty:
                logger.info(f"Actual outcome for {clip_id} already fed into benchmarks; skipping re-insert.")
                return True

            # Carry over hook_text/duration/retention-curve fields from the
            # original prediction row (logged at generation time under
            # video_id=clip_id) — the YouTube Data API doesn't expose 3s/
            # 15s/30s retention curves (that needs YouTube Analytics API
            # with a different OAuth scope), so those stay as the model's
            # own predictions; only virality_score and sample_size_views
            # get replaced with real, measured values below.
            original = ch.run_query_df(f"""
                SELECT hook_text, duration_sec, avg_3s_retention_pct,
                       avg_15s_retention_pct, avg_30s_retention_pct,
                       completion_rate_pct, crop_mode
                FROM video_hook_retention
                WHERE video_id = {ch.sql_literal(clip_id)}
                ORDER BY created_at DESC
                LIMIT 1
            """)
            if original.empty:
                logger.warning(f"No original generation record for clip_id '{clip_id}'; logging actual outcome with defaults.")
                orig = {
                    "hook_text": "", "duration_sec": 10.0, "avg_3s_retention_pct": 0.0,
                    "avg_15s_retention_pct": 0.0, "avg_30s_retention_pct": 0.0,
                    "completion_rate_pct": 0.0, "crop_mode": "unknown",
                }
            else:
                orig = original.iloc[0].to_dict()

            actual_virality_score = _compute_actual_virality_score(view_count, like_count, comment_count)

            query = (
                "INSERT INTO video_hook_retention "
                "(video_id, hook_type, hook_text, duration_sec, avg_3s_retention_pct, "
                "avg_15s_retention_pct, avg_30s_retention_pct, completion_rate_pct, "
                "virality_score, topic_category, sample_size_views, crop_mode) VALUES ("
                + ", ".join([
                    ch.sql_literal(actual_video_id),
                    ch.sql_literal(hook_type),
                    ch.sql_literal(orig["hook_text"]),
                    ch.sql_literal(float(orig["duration_sec"])),
                    ch.sql_literal(float(orig["avg_3s_retention_pct"])),
                    ch.sql_literal(float(orig["avg_15s_retention_pct"])),
                    ch.sql_literal(float(orig["avg_30s_retention_pct"])),
                    ch.sql_literal(float(orig["completion_rate_pct"])),
                    ch.sql_literal(actual_virality_score),
                    ch.sql_literal("actual_outcome"),
                    ch.sql_literal(int(view_count)),
                    ch.sql_literal(orig["crop_mode"]),
                ]) + ")"
            )
            ch.run_query(query)
            logger.info(
                f"✨ Closed feedback loop: logged real outcome for {clip_id} (hook_type={hook_type}) "
                f"with actual_virality_score={actual_virality_score:.1f} ({view_count} views, "
                f"{like_count} likes, {comment_count} comments)."
            )
            return True
        except Exception as e:
            logger.error(f"Failed to log actual outcome to benchmarks: {e}")
            return False

    def get_published_outcomes(self, limit: int = 50) -> pd.DataFrame:
        """
        Returns the latest known row per published video — predicted vs.
        actual performance, for the cross-validation view in the UI.
        """
        if not self.is_connected():
            return pd.DataFrame()
        try:
            return ch.run_query_df(f"""
            SELECT * FROM (
                SELECT
                    clip_id, youtube_video_id, youtube_url, hook_type,
                    predicted_virality_score, predicted_3s_retention_pct,
                    actual_view_count, actual_like_count, actual_comment_count,
                    last_synced_at, published_at
                FROM published_clip_outcomes
                ORDER BY youtube_video_id, published_at DESC
                LIMIT 1 BY youtube_video_id
            )
            ORDER BY published_at DESC
            LIMIT {int(limit)}
            """)
        except Exception as e:
            logger.error(f"Failed to fetch published outcomes: {e}")
            return pd.DataFrame()

    def query_hook_retention(
        self,
        hook_category: Optional[str] = None,
        min_virality: float = 0.0,
        limit: int = 50,
    ) -> pd.DataFrame:
        """
        Queries ClickHouse for historical video hook retention records.
        """
        if self.is_connected():
            try:
                where_clauses = [f"virality_score >= {float(min_virality)}"]
                if hook_category and hook_category != "all":
                    where_clauses.append(f"hook_type = {ch.sql_literal(hook_category)}")

                where_str = " AND ".join(where_clauses)
                query = f"""
                SELECT
                    video_id, hook_type, hook_text, duration_sec,
                    avg_3s_retention_pct, avg_15s_retention_pct, avg_30s_retention_pct,
                    completion_rate_pct, virality_score, topic_category, sample_size_views
                FROM video_hook_retention
                WHERE {where_str}
                ORDER BY virality_score DESC
                LIMIT {int(limit)}
                """
                return ch.run_query_df(query)
            except Exception as e:
                logger.error(f"ClickHouse query failed: {e}. Falling back to cached memory.")

        # In-memory fallback
        df = self._fallback_df.copy()
        if min_virality > 0:
            df = df[df["virality_score"] >= min_virality]
        if hook_category and hook_category != "all":
            df = df[df["hook_type"] == hook_category]
        return df.sort_values(by="virality_score", ascending=False).head(limit)

    def get_crop_mode_benchmarks(self) -> pd.DataFrame:
        """
        Aggregates performance by crop_mode (center_crop, blurred_background,
        top_anchored_crop, cinematic_letterbox). Rows logged before this
        column existed, and
        seed benchmark data, fall under 'unknown' — this view starts
        genuinely useful once a handful of real clips have been generated
        with different crop modes.
        """
        if not self.is_connected():
            return pd.DataFrame()
        try:
            return ch.run_query_df("""
            SELECT
                crop_mode,
                count() as total_samples,
                round(avg(avg_3s_retention_pct), 2) as avg_3s_retention,
                round(avg(completion_rate_pct), 2) as avg_completion_rate,
                round(avg(virality_score), 2) as avg_virality_score
            FROM video_hook_retention
            GROUP BY crop_mode
            ORDER BY avg_virality_score DESC
            """)
        except Exception as e:
            logger.error(f"ClickHouse crop_mode aggregation query failed: {e}")
            return pd.DataFrame()

    def get_distinct_topic_categories(self) -> List[str]:
        """Returns known topic_category values, for the Topic Focus UI dropdown."""
        if self.is_connected():
            try:
                result = ch.run_query_df(
                    "SELECT DISTINCT topic_category FROM video_hook_retention ORDER BY topic_category"
                )
                if not result.empty:
                    return result["topic_category"].tolist()
            except Exception as e:
                logger.error(f"Failed to fetch distinct topic categories: {e}")
        return sorted(self._fallback_df["topic_category"].unique().tolist())

    def get_hook_type_benchmarks(self, topic_category: Optional[str] = None) -> pd.DataFrame:
        """
        Calculates aggregated retention performance across hook taxonomies,
        optionally filtered to a single topic_category so grounding data
        can be scoped to a relevant slice of history instead of everything.
        """
        if self.is_connected():
            try:
                where_clause = ""
                if topic_category and topic_category != "all":
                    where_clause = f"WHERE topic_category = {ch.sql_literal(topic_category)}"
                query = f"""
                SELECT
                    hook_type,
                    count() as total_samples,
                    round(avg(avg_3s_retention_pct), 2) as avg_3s_retention,
                    round(avg(avg_15s_retention_pct), 2) as avg_15s_retention,
                    round(avg(avg_30s_retention_pct), 2) as avg_30s_retention,
                    round(avg(completion_rate_pct), 2) as avg_completion_rate,
                    round(avg(virality_score), 2) as avg_virality_score,
                    round(avg(duration_sec), 1) as avg_duration_sec
                FROM video_hook_retention
                {where_clause}
                GROUP BY hook_type
                ORDER BY avg_virality_score DESC
                """
                return ch.run_query_df(query)
            except Exception as e:
                logger.error(f"ClickHouse aggregation query failed: {e}")

        # Fallback aggregation
        df = self._fallback_df
        if topic_category and topic_category != "all":
            df = df[df["topic_category"] == topic_category]
        grouped = df.groupby("hook_type").agg(
            total_samples=("video_id", "count"),
            avg_3s_retention=("avg_3s_retention_pct", "mean"),
            avg_15s_retention=("avg_15s_retention_pct", "mean"),
            avg_30s_retention=("avg_30s_retention_pct", "mean"),
            avg_completion_rate=("completion_rate_pct", "mean"),
            avg_virality_score=("virality_score", "mean"),
            avg_duration_sec=("duration_sec", "mean"),
        ).reset_index()
        return grouped.round(2).sort_values(by="avg_virality_score", ascending=False)

    def get_retention_intelligence_summary(self, topic_category: Optional[str] = None) -> Dict[str, Any]:
        """
        Generates a concise intelligence payload for the Gemini prompt.
        When topic_category is set, grounding data is scoped to that
        category — but if the category has too little history to be
        useful (empty result), this falls back to the full unfiltered
        benchmark set rather than handing Gemini an empty taxonomy.
        """
        benchmarks_df = self.get_hook_type_benchmarks(topic_category=topic_category)
        used_fallback_scope = False
        if benchmarks_df.empty and topic_category and topic_category != "all":
            logger.warning(f"No ClickHouse history for topic_category '{topic_category}'; falling back to all categories.")
            benchmarks_df = self.get_hook_type_benchmarks()
            used_fallback_scope = True

        summary_list = []
        for _, row in benchmarks_df.iterrows():
            summary_list.append({
                "hook_type": row["hook_type"],
                "avg_3s_retention": f"{row['avg_3s_retention']}%",
                "avg_3s_retention_value": float(row["avg_3s_retention"]),
                "avg_completion_rate": float(row["avg_completion_rate"]),
                "avg_virality_score": row["avg_virality_score"],
                "optimal_duration_sec": row["avg_duration_sec"],
            })

        best_hook = benchmarks_df.iloc[0]["hook_type"] if not benchmarks_df.empty else "shock_stat"

        return {
            "top_performing_hook_type": best_hook,
            "overall_avg_3s_retention": float(benchmarks_df["avg_3s_retention"].mean()) if not benchmarks_df.empty else 88.5,
            "recommended_clip_duration_range": "25s - 45s",
            "topic_focus": topic_category if (topic_category and topic_category != "all" and not used_fallback_scope) else None,
            "topic_focus_had_no_history": used_fallback_scope,
            "hook_taxonomies": summary_list,
            "key_insights": [
                "Shock Stats and Curiosity Gaps generate >92% 3-second hold rate.",
                "Optimal clip length for maximum completion rate is between 28s and 42s.",
                "Contrarian claims maintain the strongest 15-to-30s retention curve."
            ]
        }

    def execute_custom_query(self, query: str) -> pd.DataFrame:
        """
        Executes a custom SQL query against ClickHouse (for the explorer
        UI). This is user-typed SQL running with CLICKHOUSE_ALLOW_WRITE_ACCESS
        enabled, so per clickhouse-best-practices' agent-query-safety rule
        (CRITICAL), conservative scan/time caps are appended unless the
        query already specifies its own SETTINGS clause — an unbounded
        query (or a fat-fingered cross join) could otherwise scan without
        limit, which matters more once this console is reachable from a
        public deployment rather than just a local session.
        """
        if not self.is_connected():
            raise ConnectionError("ClickHouse MCP server is not connected.")
        safe_query = query.strip().rstrip(";")
        if "SETTINGS" not in safe_query.upper():
            safe_query += (
                " SETTINGS max_execution_time = 30, max_rows_to_read = 1000000000, "
                "timeout_before_checking_execution_speed = 0, max_result_rows = 10000, "
                "result_overflow_mode = 'break'"
            )
        return ch.run_query_df(safe_query)