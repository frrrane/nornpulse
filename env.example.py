# nornpulse/config.py
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Environment & Demo Controls
    DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"
    
    # Dynamic Input Source (Falls back to public-domain Carl Sagan asset in demo mode)
    INPUT_VIDEO_SOURCE = (
        "https://upload.wikimedia.org/wikipedia/commons/transcript/c/c2/Carl_Sagan_Senate_Speech_1985_%28Excerpt%29.webm"
        if DEMO_MODE 
        else os.getenv("INPUT_VIDEO_SOURCE", "")
    )
    
    # ClickHouse Partner Integration Settings
    CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
    CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", 8123))
    CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "default")
    CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
    CLICKHOUSE_DATABASE = os.getenv("CLICKHOUSE_DATABASE", "default")