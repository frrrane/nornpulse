import os
from pathlib import Path
from typing import List, Optional
import yt_dlp

def download_youtube_video(url: str, output_dir: str = "sample_data", output_filename: Optional[str] = None) -> str:
    """
    Downloads a public YouTube video as an MP4 to use as a test asset for
    the NornPulse pipeline. output_filename defaults to the fixed
    "yt_input.mp4" (overwritten on every call) for the single-video
    workflow; batch mode passes a unique filename per video so
    concurrent/sequential downloads don't clobber each other before
    they've each been processed.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_template = os.path.join(output_dir, output_filename or "yt_input.mp4")

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


def list_playlist_video_urls(playlist_or_channel_url: str, max_videos: int = 3) -> List[str]:
    """
    Enumerates up to max_videos video URLs from a YouTube playlist or
    channel URL, for batch mode. Uses yt-dlp's flat-playlist extraction —
    metadata only, no video downloads — so listing is cheap even against
    a channel with hundreds of uploads; only the first max_videos entries
    are returned.
    """
    ydl_opts = {
        'extract_flat': True,
        'playlistend': max_videos,
        'quiet': True,
        'skip_download': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(playlist_or_channel_url, download=False)

    entries = info.get('entries', []) if info else []
    urls = []
    for entry in entries[:max_videos]:
        if not entry:
            continue
        video_id = entry.get('id')
        url = entry.get('url') or (f"https://www.youtube.com/watch?v={video_id}" if video_id else None)
        if url:
            urls.append(url)
    return urls


if __name__ == "__main__":
    test_url = input("Enter a YouTube URL to test: ")
    if test_url:
        download_youtube_video(test_url)
