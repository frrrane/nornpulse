"""
Urðr Analytics Tool (ᚢ - Urðr / The Past)
Part of NornPulse: Autonomous Media Engine by Norn Labs (nornlabs.ai)

Urðr weaves the thread of the past. This module queries ClickHouse for historical
video hook retention metrics, drop-off curves, and virality benchmarks to ground
AI clip decisions in empirical audience behavioral data. It also logs new generation
telemetry to close the autonomous feedback loop.
"""

import os
import logging
import datetime
from typing import Dict, Any, List, Optional
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from config import Config  # noqa: E402 – imported after load_dotenv intentionally

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


class UrdrAnalytics:
    """
    Urðr: Past Video Hook Analytics & ClickHouse Intelligence Tool.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        database: Optional[str] = None,
    ):
        self.host = host or Config.CLICKHOUSE_HOST
        self.port = int(port or Config.CLICKHOUSE_PORT)
        self.username = username or Config.CLICKHOUSE_USER
        self.password = password or Config.CLICKHOUSE_PASSWORD
        self.database = database or Config.CLICKHOUSE_DATABASE


        self.client = None
        self._connected = False
        self._fallback_df = pd.DataFrame(DEFAULT_HOOK_BENCHMARKS)
        self.connect()

    def connect(self) -> bool:
        """Attempts connection to ClickHouse server via clickhouse-connect."""
        try:
            import clickhouse_connect

            self.client = clickhouse_connect.get_client(
                host=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                database=self.database,
                connect_timeout=3,
                send_receive_timeout=5,
            )
            # Test query
            self.client.command("SELECT 1")
            self._connected = True
            logger.info(f"⚡ Urðr successfully connected to ClickHouse at {self.host}:{self.port}/{self.database}")
            self.init_schema()
            return True
        except Exception as e:
            self._connected = False
            self.client = None
            logger.warning(f"⚠️ Urðr ClickHouse connection unavailable ({e}). Operating in resilient fallback mode.")
            return False

    def is_connected(self) -> bool:
        """Returns whether a live ClickHouse connection is active."""
        if not self._connected or not self.client:
            return False
        try:
            self.client.command("SELECT 1")
            return True
        except Exception:
            self._connected = False
            return False

    def init_schema(self) -> None:
        """Initializes database schema and populates seed dataset if empty."""
        if not self.is_connected():
            return

        try:
            # Create video_hook_retention table
            create_table_query = """
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
            """
            self.client.command(create_table_query)

            # Check if empty
            count = self.client.command("SELECT count() FROM video_hook_retention")
            if count == 0:
                self.seed_benchmarks()
        except Exception as e:
            logger.error(f"Error initializing ClickHouse schema: {e}")

    def seed_benchmarks(self) -> int:
        """Seeds standard video hook benchmarks into ClickHouse."""
        if not self.is_connected():
            return len(self._fallback_df)

        try:
            rows = [
                (
                    b["video_id"],
                    b["hook_type"],
                    b["hook_text"],
                    b["duration_sec"],
                    b["avg_3s_retention_pct"],
                    b["avg_15s_retention_pct"],
                    b["avg_30s_retention_pct"],
                    b["completion_rate_pct"],
                    b["virality_score"],
                    b["topic_category"],
                    b["sample_size_views"],
                )
                for b in DEFAULT_HOOK_BENCHMARKS
            ]
            self.client.insert(
                "video_hook_retention",
                rows,
                column_names=[
                    "video_id",
                    "hook_type",
                    "hook_text",
                    "duration_sec",
                    "avg_3s_retention_pct",
                    "avg_15s_retention_pct",
                    "avg_30s_retention_pct",
                    "completion_rate_pct",
                    "virality_score",
                    "topic_category",
                    "sample_size_views",
                ],
            )
            logger.info(f"Seeded {len(rows)} retention benchmark records into ClickHouse.")
            return len(rows)
        except Exception as e:
            logger.error(f"Failed to seed benchmarks into ClickHouse: {e}")
            return 0
            
    def log_generated_clip(
        self, 
        clip_id: str, 
        hook_type: str, 
        hook_text: str, 
        duration_sec: float, 
        predicted_3s: float, 
        predicted_completion: float,
        virality_score: float,
        topic_category: str = "generated_clip"
    ) -> bool:
        """
        Closes the loop by logging newly generated clips and their predicted 
        telemetry back into the ClickHouse database.
        """
        if not self.is_connected():
            logger.warning("ClickHouse disconnected; unable to log generated clip.")
            return False
            
        try:
            # We seed new rows with 0 sample_size until actual telemetry is retrieved
            row = (
                clip_id,
                hook_type,
                hook_text,
                float(duration_sec),
                float(predicted_3s),
                0.0, # 15s prediction placeholder
                0.0, # 30s prediction placeholder
                float(predicted_completion),
                float(virality_score),
                topic_category,
                0, # Sample size views starts at 0
            )
            
            self.client.insert(
                "video_hook_retention",
                [row],
                column_names=[
                    "video_id",
                    "hook_type",
                    "hook_text",
                    "duration_sec",
                    "avg_3s_retention_pct",
                    "avg_15s_retention_pct",
                    "avg_30s_retention_pct",
                    "completion_rate_pct",
                    "virality_score",
                    "topic_category",
                    "sample_size_views",
                ],
            )
            logger.info(f"Logged generated clip {clip_id} prediction telemetry to ClickHouse.")
            return True
        except Exception as e:
            logger.error(f"Failed to log generated clip to ClickHouse: {e}")
            return False

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
                    where_clauses.append(f"hook_type = '{hook_category}'")
                
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
                return self.client.query_df(query)
            except Exception as e:
                logger.error(f"ClickHouse query failed: {e}. Falling back to cached memory.")

        # In-memory fallback
        df = self._fallback_df.copy()
        if min_virality > 0:
            df = df[df["virality_score"] >= min_virality]
        if hook_category and hook_category != "all":
            df = df[df["hook_type"] == hook_category]
        return df.sort_values(by="virality_score", ascending=False).head(limit)

    def get_hook_type_benchmarks(self) -> pd.DataFrame:
        """
        Calculates aggregated retention performance across hook taxonomies.
        """
        if self.is_connected():
            try:
                query = """
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
                GROUP BY hook_type
                ORDER BY avg_virality_score DESC
                """
                return self.client.query_df(query)
            except Exception as e:
                logger.error(f"ClickHouse aggregation query failed: {e}")

        # Fallback aggregation
        grouped = self._fallback_df.groupby("hook_type").agg(
            total_samples=("video_id", "count"),
            avg_3s_retention=("avg_3s_retention_pct", "mean"),
            avg_15s_retention=("avg_15s_retention_pct", "mean"),
            avg_30s_retention=("avg_30s_retention_pct", "mean"),
            avg_completion_rate=("completion_rate_pct", "mean"),
            avg_virality_score=("virality_score", "mean"),
            avg_duration_sec=("duration_sec", "mean"),
        ).reset_index()
        return grouped.round(2).sort_values(by="avg_virality_score", ascending=False)

    def get_retention_intelligence_summary(self) -> Dict[str, Any]:
        """
        Generates a concise intelligence payload for Gemini 2.0 Flash prompt context.
        """
        benchmarks_df = self.get_hook_type_benchmarks()
        summary_list = []
        for _, row in benchmarks_df.iterrows():
            summary_list.append({
                "hook_type": row["hook_type"],
                "avg_3s_retention": f"{row['avg_3s_retention']}%",
                "avg_virality_score": row["avg_virality_score"],
                "optimal_duration_sec": row["avg_duration_sec"],
            })

        best_hook = benchmarks_df.iloc[0]["hook_type"] if not benchmarks_df.empty else "shock_stat"
        
        return {
            "top_performing_hook_type": best_hook,
            "overall_avg_3s_retention": float(benchmarks_df["avg_3s_retention"].mean()) if not benchmarks_df.empty else 88.5,
            "recommended_clip_duration_range": "25s - 45s",
            "hook_taxonomies": summary_list,
            "key_insights": [
                "Shock Stats and Curiosity Gaps generate >92% 3-second hold rate.",
                "Optimal clip length for maximum completion rate is between 28s and 42s.",
                "Contrarian claims maintain the strongest 15-to-30s retention curve."
            ]
        }

    def execute_custom_query(self, query: str) -> pd.DataFrame:
        """Executes a custom SQL query against ClickHouse (for the explorer UI)."""
        if not self.is_connected():
            raise ConnectionError("ClickHouse server is not connected.")
        return self.client.query_df(query)