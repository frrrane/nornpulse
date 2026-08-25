import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import yt_dlp


def _source_sidecar(video_path: str) -> Path:
    """Where a downloaded file records which URL and range produced it."""
    return Path(str(video_path) + ".source.json")


def _source_fingerprint(url: str, time_range: Optional[Tuple[float, float]]) -> Dict[str, Any]:
    # Rounded, because a window start computed in floating point will not
    # compare equal to itself across runs.
    return {
        "url": url,
        "time_range": ([round(float(t), 3) for t in time_range] if time_range else None),
    }


def _already_downloaded(target: str, want: Dict[str, Any]) -> bool:
    """
    Whether target is a usable copy of exactly this URL and range.

    The output filename is fixed per slot ("yt_input.mp4",
    "batch_0_input.mp4"), so an existing file is NOT evidence that it holds
    the video being asked for -- it is just as likely to be the previous
    one. Reusing it on the strength of the name alone would cut a different
    source against the right transcript, and every clip would render
    cleanly while being about the wrong video. Hence the recorded
    fingerprint: reuse only on an exact match, and re-download otherwise.
    """
    path = Path(target)
    if not path.exists() or path.stat().st_size == 0:
        return False
    sidecar = _source_sidecar(target)
    if not sidecar.exists():
        return False
    try:
        return json.loads(sidecar.read_text(encoding="utf-8")) == want
    except (json.JSONDecodeError, OSError):
        return False


def get_youtube_duration(url: str) -> float:
    """
    Cheap metadata-only probe for a YouTube video's duration — no
    download. Used to decide, BEFORE spending any download time/
    bandwidth, whether a video needs a bounded time_range (see
    download_youtube_video) rather than downloading the whole thing only
    to window it down afterward.
    """
    ydl_opts = {'quiet': True, 'skip_download': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return float(info.get('duration') or 0.0)


def download_youtube_video(
    url: str, output_dir: str = "sample_data", output_filename: Optional[str] = None,
    time_range: Optional[Tuple[float, float]] = None, reuse: bool = True,
) -> str:
    """
    Downloads a public YouTube video as an MP4 to use as a test asset for
    the NornPulse pipeline. output_filename defaults to the fixed
    "yt_input.mp4" (overwritten on every call) for the single-video
    workflow; batch mode passes a unique filename per video so
    concurrent/sequential downloads don't clobber each other before
    they've each been processed.

    time_range, if given as (start_sec, end_sec), downloads ONLY that
    section via yt-dlp's download_ranges — confirmed live against a real
    94-minute video: a 30s range downloaded in ~15s as a ~1.8MB file,
    instead of pulling the full ~180MB video just to discard 99% of it
    afterward. This is what actually makes the long-video auto-window
    feature (agent/verdandi_orchestrator.py's AUTO_WINDOW_MAX_SEC) cheap
    — bounding what gets reasoned over doesn't help if the whole file
    still has to be downloaded first.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_template = os.path.join(output_dir, output_filename or "yt_input.mp4")

    # Re-downloading a ten-minute window costs about ninety seconds of
    # bandwidth and re-encoding every time the pipeline is retried, which
    # during a debugging loop is most of the wall clock. Skipped only when
    # the file on disk is provably this URL and this range.
    fingerprint = _source_fingerprint(url, time_range)
    if reuse and _already_downloaded(output_template, fingerprint):
        print(f"Reusing already-downloaded {output_template}")
        return output_template

    # Cap the pull at 1080p. The output is a 1080x1920 vertical crop taken
    # from the centre of the frame, so anything above 1080p is downscaled
    # and thrown away — and on a long documentary "best" can mean 4K, which
    # turns a bounded window into a multi-gigabyte download and a Gemini
    # upload to match.
    ydl_opts = {
        'format': ('bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/'
                   'best[ext=mp4][height<=1080]/best[ext=mp4]'),
        'outtmpl': output_template,
        'overwrites': True,
        # Add client impersonation to bypass 403 bot blocks
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
    }
    if time_range:
        start, end = time_range
        ydl_opts['download_ranges'] = yt_dlp.utils.download_range_func(None, [(start, end)])
        ydl_opts['force_keyframes_at_cuts'] = True

    print(f"Downloading video from {url}" + (f" (range {time_range[0]:.0f}-{time_range[1]:.0f}s)" if time_range else "") + "...")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    # Written only after a successful download, so an interrupted one is
    # never mistaken for a complete cache entry.
    _source_sidecar(output_template).write_text(
        json.dumps(fingerprint), encoding="utf-8")
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
