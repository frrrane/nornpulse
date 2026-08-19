import clickhouse_connect
from datetime import datetime

def get_clickhouse_client():
    """
    Initializes a connection to a local or remote ClickHouse instance.
    """
    client = clickhouse_connect.get_client(
        host='localhost', 
        port=8123, 
        username='default', 
        password=''
    )
    return client

def init_telemetry_table():
    """
    Ensures the telemetry tracking table exists in ClickHouse.
    """
    client = get_clickhouse_client()
    client.command("""
        CREATE TABLE IF NOT EXISTS nornpulse_telemetry (
            timestamp DateTime,
            video_name String,
            duration_seconds Float32,
            status String,
            agent_stage String
        ) ENGINE = MergeTree()
        ORDER BY timestamp
    """)
    print("ClickHouse telemetry table verified/created.")

def log_render_event(video_name: str, duration: float, status: str, stage: str):
    """
    Logs an individual pipeline execution event to ClickHouse.
    """
    client = get_clickhouse_client()
    data = [[datetime.now(), video_name, duration, status, stage]]
    client.insert(
        'nornpulse_telemetry', 
        data, 
        column_names=['timestamp', 'video_name', 'duration_seconds', 'status', 'agent_stage']
    )
    print(f"Logged event to ClickHouse: {video_name} [{status}]")

if __name__ == "__main__":
    try:
        init_telemetry_table()
        log_render_event("yt_input.mp4", 30.0, "SUCCESS", "Skuld_Compiler")
    except Exception as e:
        print(f"ClickHouse connection note (ensure local instance is running if testing live): {e}")
