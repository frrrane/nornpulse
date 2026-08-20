"""
⚡ NornPulse: Configuration Module (config.py)
Norn Labs (nornlabs.ai)

Reads environment variables (via .env or host environment) and exposes a
single Config class for use across the entire application.

Demo Mode:
  When DEMO_MODE=true (the default), INPUT_VIDEO_SOURCE automatically falls
  back to the public-domain Carl Sagan Senate Speech excerpt hosted on
  Wikimedia Commons, so the pipeline runs out-of-the-box without any local
  asset setup.
"""

import os
from dotenv import load_dotenv

# Load variables from a .env file if one is present (no-op if absent)
load_dotenv()

# ---------------------------------------------------------------------------
# Public-domain demo asset (Carl Sagan Senate Speech, 1985 – Wikimedia Commons)
# ---------------------------------------------------------------------------
_DEMO_VIDEO_URL = (
    "https://upload.wikimedia.org/wikipedia/commons/transcoded/c/c2/"
    "Carl_Sagan_Senate_Speech_1985_%28Excerpt%29.webm/"
    "Carl_Sagan_Senate_Speech_1985_%28Excerpt%29.webm.480p.vp9.webm"
)


class Config:
    """Central configuration object for NornPulse.

    All values are resolved once at import time from environment variables,
    with sensible defaults.  Import this class (or its attributes) instead
    of calling os.getenv() inline throughout the codebase.
    """

    # ------------------------------------------------------------------
    # Demo / environment controls
    # ------------------------------------------------------------------
    DEMO_MODE: bool = os.getenv("DEMO_MODE", "true").lower() in ("1", "true", "yes")

    # ------------------------------------------------------------------
    # Input video source
    #   • In DEMO_MODE  → public-domain Carl Sagan asset (no local file needed)
    #   • Otherwise     → value of INPUT_VIDEO_SOURCE env var (URL or file path)
    # ------------------------------------------------------------------
    INPUT_VIDEO_SOURCE: str = (
        _DEMO_VIDEO_URL
        if DEMO_MODE
        else os.getenv("INPUT_VIDEO_SOURCE", "")
    )

    # ------------------------------------------------------------------
    # ClickHouse connection settings
    #
    # NOTE: these are exposed here for reference/display only. The actual
    # ClickHouse MCP server (mcp-clickhouse) runs as its own subprocess and
    # reads CLICKHOUSE_* straight from the environment itself (see
    # agent/clickhouse_mcp_client.py) — it does not go through this Config
    # object, since it's a separate process with its own env.
    # ------------------------------------------------------------------
    CLICKHOUSE_HOST: str = os.getenv("CLICKHOUSE_HOST", "localhost")
    CLICKHOUSE_PORT: int = int(os.getenv("CLICKHOUSE_PORT", "8123"))
    CLICKHOUSE_USER: str = os.getenv("CLICKHOUSE_USER", "default")
    CLICKHOUSE_PASSWORD: str = os.getenv("CLICKHOUSE_PASSWORD", "")
    CLICKHOUSE_DATABASE: str = os.getenv("CLICKHOUSE_DATABASE", "default")

    # ------------------------------------------------------------------
    # Video duration constraints
    #
    #   DEFAULT_VIDEO_DURATION_SEC  – target output clip length when no
    #       explicit start/end window is supplied (default: 10 s, safe
    #       mid-point of the 5–15 s standard range).
    #
    #   MIN_VIDEO_DURATION_SEC      – hard lower bound; clips shorter than
    #       this are rejected / padded (default: 5 s).
    #
    #   MAX_VIDEO_DURATION_SEC      – upper bound in standard mode
    #       (default: 15 s).
    #
    #   EXTENDED_DURATION_MODE      – set to "true" in .env to unlock
    #       clips up to MAX_EXTENDED_DURATION_SEC (default: 30 s).
    #       Useful for long-form repurposing without changing defaults.
    #
    #   EFFECTIVE_MAX_DURATION_SEC  – computed ceiling actually used by
    #       the pipeline; always read this rather than the raw MAX vars.
    # ------------------------------------------------------------------
    DEFAULT_VIDEO_DURATION_SEC: float = float(
        os.getenv("DEFAULT_VIDEO_DURATION_SEC", "10")
    )
    MIN_VIDEO_DURATION_SEC: float = float(
        os.getenv("MIN_VIDEO_DURATION_SEC", "5")
    )
    MAX_VIDEO_DURATION_SEC: float = float(
        os.getenv("MAX_VIDEO_DURATION_SEC", "15")
    )
    MAX_EXTENDED_DURATION_SEC: float = float(
        os.getenv("MAX_EXTENDED_DURATION_SEC", "30")
    )
    EXTENDED_DURATION_MODE: bool = os.getenv(
        "EXTENDED_DURATION_MODE", "false"
    ).lower() in ("1", "true", "yes")

    # Single value the pipeline consumes – no magic numbers elsewhere
    EFFECTIVE_MAX_DURATION_SEC: float = (
        float(os.getenv("MAX_EXTENDED_DURATION_SEC", "30"))
        if os.getenv("EXTENDED_DURATION_MODE", "false").lower() in ("1", "true", "yes")
        else float(os.getenv("MAX_VIDEO_DURATION_SEC", "15"))
    )

    # ------------------------------------------------------------------
    # Application / logging settings
    # ------------------------------------------------------------------
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    APP_PORT: int = int(os.getenv("APP_PORT", "8501"))