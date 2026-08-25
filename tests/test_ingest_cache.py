"""
Unit tests for reusing an already-downloaded source video.

No network. The property worth guarding is not the saving -- it is the
refusal to reuse: the output filename is fixed per slot, so an existing
file is not evidence that it holds the video being asked for. Reusing one
on the strength of its name would cut a different source against the right
transcript, and every clip would render cleanly while being about the
wrong video.
"""

import json

import pytest

from utils import ingest


@pytest.fixture
def downloaded(tmp_path, monkeypatch):
    """A fake previous download of URL_A, windowed 0-600s."""
    video = tmp_path / "yt_input.mp4"
    video.write_bytes(b"not really an mp4, but not empty either")
    ingest._source_sidecar(str(video)).write_text(
        json.dumps(ingest._source_fingerprint("https://example.com/A", (0.0, 600.0))))
    return video


def _reused(video, url, time_range):
    return ingest._already_downloaded(
        str(video), ingest._source_fingerprint(url, time_range))


def test_the_same_url_and_window_is_reused(downloaded):
    assert _reused(downloaded, "https://example.com/A", (0.0, 600.0))


def test_a_different_url_is_never_reused(downloaded):
    """The whole point: same filename, different video."""
    assert not _reused(downloaded, "https://example.com/B", (0.0, 600.0))


def test_a_different_window_is_never_reused(downloaded):
    assert not _reused(downloaded, "https://example.com/A", (600.0, 1200.0))


def test_a_windowed_file_is_not_reused_for_the_whole_video(downloaded):
    assert not _reused(downloaded, "https://example.com/A", None)


def test_float_noise_in_the_window_still_matches(downloaded):
    """A window start recomputed in floating point must not miss."""
    assert _reused(downloaded, "https://example.com/A", (0.0000001, 599.9999999))


def test_an_empty_file_is_not_reused(downloaded):
    downloaded.write_bytes(b"")
    assert not _reused(downloaded, "https://example.com/A", (0.0, 600.0))


def test_a_missing_file_is_not_reused(downloaded):
    downloaded.unlink()
    assert not _reused(downloaded, "https://example.com/A", (0.0, 600.0))


def test_a_file_with_no_sidecar_is_not_reused(tmp_path):
    """Anything downloaded before this existed has unknown provenance."""
    orphan = tmp_path / "yt_input.mp4"
    orphan.write_bytes(b"from some earlier run")
    assert not _reused(orphan, "https://example.com/A", (0.0, 600.0))


def test_a_corrupt_sidecar_is_not_reused(downloaded):
    ingest._source_sidecar(str(downloaded)).write_text("{not json")
    assert not _reused(downloaded, "https://example.com/A", (0.0, 600.0))


def test_download_skips_yt_dlp_entirely_on_a_hit(downloaded, monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("yt_dlp should not have been constructed")
    monkeypatch.setattr(ingest.yt_dlp, "YoutubeDL", _boom)

    out = ingest.download_youtube_video(
        "https://example.com/A", output_dir=str(downloaded.parent),
        output_filename="yt_input.mp4", time_range=(0.0, 600.0))
    assert out == str(downloaded)


def test_reuse_can_be_turned_off(downloaded, monkeypatch):
    calls = {"n": 0}

    class _FakeYDL:
        def __init__(self, opts): calls["n"] += 1
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def download(self, urls): downloaded.write_bytes(b"fresh bytes")

    monkeypatch.setattr(ingest.yt_dlp, "YoutubeDL", _FakeYDL)
    ingest.download_youtube_video(
        "https://example.com/A", output_dir=str(downloaded.parent),
        output_filename="yt_input.mp4", time_range=(0.0, 600.0), reuse=False)
    assert calls["n"] == 1


def test_a_fresh_download_records_its_source(tmp_path, monkeypatch):
    target = tmp_path / "yt_input.mp4"

    class _FakeYDL:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def download(self, urls): target.write_bytes(b"fresh bytes")

    monkeypatch.setattr(ingest.yt_dlp, "YoutubeDL", _FakeYDL)
    ingest.download_youtube_video(
        "https://example.com/C", output_dir=str(tmp_path),
        output_filename="yt_input.mp4", time_range=(5.0, 65.0))

    recorded = json.loads(ingest._source_sidecar(str(target)).read_text())
    assert recorded == {"url": "https://example.com/C", "time_range": [5.0, 65.0]}


def test_an_interrupted_download_leaves_no_cache_entry(tmp_path, monkeypatch):
    """A half-written file must not be mistaken for a complete one."""
    class _FakeYDL:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def download(self, urls): raise KeyboardInterrupt("user gave up")

    monkeypatch.setattr(ingest.yt_dlp, "YoutubeDL", _FakeYDL)
    with pytest.raises(KeyboardInterrupt):
        ingest.download_youtube_video(
            "https://example.com/D", output_dir=str(tmp_path),
            output_filename="yt_input.mp4")
    assert not ingest._source_sidecar(str(tmp_path / "yt_input.mp4")).exists()
