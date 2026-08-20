import time
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from agent.norn_cron import run_headless_pipeline

logging.basicConfig(level=logging.INFO)

def scheduled_job():
    print("⏰ Cron triggered: Starting daily NornPulse media generation batch...")
    try:
        run_headless_pipeline("sample_data/yt_input.mp4", "Daily Automated Batch", target_clips=2)
    except Exception as e:
        print(f"❌ Scheduled job failed: {e}")

if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    # Run every day at 8:00 AM (or adjust trigger as needed for testing)
    scheduler.add_job(scheduled_job, 'interval', hours=24)
    scheduler.start()

    print("⚡ NornPulse Background Automation Daemon started. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        print("Daemon stopped.")