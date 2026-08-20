# test_hitl.py
from pathlib import Path
from agent.norn_cron import run_headless_pipeline
from agent.norn_publisher import NornPublisher

def test_pipeline_and_email():
    print("🚀 Starting manual NornPulse HITL test...")
    
    # Ensure sample video exists
    sample_vid = "sample_data/yt_input.mp4"
    if not Path(sample_vid).exists():
        print("❌ Sample video not found. Please generate or provide sample_data/yt_input.mp4 first.")
        return

    # 1. Run the headless pipeline (Urðr + Verðandi + Skuld)
    rendered_paths = run_headless_pipeline(
        video_path=sample_vid, 
        video_title="Manual Staging Test", 
        target_clips=1
    )
    
    if not rendered_paths:
        print("❌ Pipeline failed to render any clips.")
        return

    # 2. Trigger Gmail Staging Notification
    publisher = NornPublisher()
    for path in rendered_paths:
        clip_id = Path(path).stem
        print(f"📧 Sending test Gmail staging email for {clip_id}...")
        
        success = publisher.send_gmail_staged_approval(
            clip_id=clip_id,
            title="Manual Staging Test Hook",
            virality=95.0,
            video_path=path
        )
        
        if success:
            print("✅ Test email successfully sent! Check your inbox.")
        else:
            print("❌ Failed to send email. Check your terminal logs for errors.")

if __name__ == "__main__":
    test_pipeline_and_email()