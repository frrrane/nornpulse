# approve_and_publish.py
from pathlib import Path
from agent.norn_publisher import NornPublisher

def publish_latest_short():
    print("🚀 Initiating YouTube Shorts upload for staged clip...")
    
    # Find the most recently rendered 9:16 short in your output directory
    output_dir = Path("output_clips") # or whichever folder Skuld saves your vertical renders to
    mp4_files = list(output_dir.glob("*_9x16.mp4"))
    
    if not mp4_files:
        print("❌ No staged 9:16 shorts found in output directory.")
        return

    # Pick the latest file
    latest_clip = max(mp4_files, key=lambda p: p.stat().st_mtime)
    print(f"🎬 Selected clip for upload: {latest_clip.name}")

    publisher = NornPublisher()
    
    # Title and description for YouTube
    title = "The 93% AI Reality Check"
    description = "Generated autonomously by NornPulse (nornlabs.ai)"

    # Execute upload (This will trigger the local OAuth browser window on first run)
    video_id = publisher.upload_to_youtube_shorts(
        video_path=latest_clip,
        title=title,
        description=description
    )

    if video_id:
        print(f"✨ Successfully published! Watch on YouTube Shorts: https://youtube.com/shorts/{video_id}")
    else:
        print("❌ Upload failed. Check your logs and client_secrets.json configuration.")

if __name__ == "__main__":
    publish_latest_short()