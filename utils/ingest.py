import os
from pathlib import Path
import yt_dlp

def download_youtube_video(url: str, output_dir: str = "sample_data") -> str:
    """
    Downloads a public YouTube video as an MP4 to use as a test asset for the NornPulse pipeline.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_template = os.path.join(output_dir, "yt_input.mp4")
    
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]',
        'outtmpl': output_template,
        'overwrites': True,
        # Add client impersonation to bypass 403 bot blocks
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
    }
    
    print(f"Downloading video from {url}...")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
        
    print(f"Video successfully saved to {output_template}")
    return output_template

if __name__ == "__main__":
    test_url = input("Enter a YouTube URL to test: ")
    if test_url:
        download_youtube_video(test_url)
