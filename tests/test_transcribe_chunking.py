"""
Transcript timestamps must survive a long video.

A 22-minute NASA source was transcribed in one call and its timestamps were
accurate at 1, 5 and 10 minutes, then 88 seconds early by the 16-minute mark —
so a clip cut there was captioned with words spoken a minute and a half
earlier. The same run silently ended at 19:07 on a 22:16 video: the dropped
material is what pulls the later timestamps early.

Three separate caption fixes were made before the timeline itself was checked.
These guard the chunking that bounds the error.
"""

import re

import pytest

from utils import transcribe as tr


def test_a_chunks_timestamps_are_moved_into_source_time():
    got = tr._shift_timestamps("[00:06.880] hello\n[07:00.000] world", 420.0)
    assert got == "[07:06.880] hello\n[14:00.000] world"


def test_zero_offset_is_a_round_trip():
    assert tr._shift_timestamps("[01:02.500] x", 0.0) == "[01:02.500] x"


def test_minutes_pass_sixty_rather_than_wrapping():
    """A 40-minute source needs [42:…], not [02:…] on the third chunk."""
    got = tr._shift_timestamps("[05:00.000] late", 2220.0)
    assert got == "[42:00.000] late"


def test_text_around_the_timestamp_is_untouched():
    line = "[00:01.000] a line with [brackets] and 12:34 inside it"
    got = tr._shift_timestamps(line, 60.0)
    assert got.endswith("a line with [brackets] and 12:34 inside it")
    assert got.startswith("[01:01.000]")


@pytest.mark.parametrize("offset", [0.0, 7.5, 420.0, 1260.0])
def test_shifted_timestamps_stay_parseable(offset):
    from agent.skuld_renderer import parse_time_to_seconds
    got = tr._shift_timestamps("[03:20.400] x", offset)
    stamp = re.match(r"\[([^\]]+)\]", got).group(1)
    assert parse_time_to_seconds(stamp) == pytest.approx(200.4 + offset, abs=0.01)


def test_chunking_only_kicks_in_for_long_videos():
    """A short source keeps the single call it always had."""
    assert tr.TRANSCRIBE_CHUNK_SEC < tr.TRANSCRIBE_CHUNK_THRESHOLD_SEC
    assert tr.TRANSCRIBE_CHUNK_THRESHOLD_SEC >= 300


# --- the cache must answer about THIS video --------------------------------
#
# Keyed on the filename alone, it handed one video another's words. Every
# download lands at the same fixed path (sample_data/yt_input.mp4), so a new
# source inherited the previous one's transcript, and the only check was
# whether the file looked like a transcript — which a wrong one does. A
# 161-second Cosmos video ended up paired with a 22-minute Artemis transcript.

def test_two_videos_at_the_same_path_get_different_cache_files(tmp_path):
    same_path = tmp_path / "yt_input.mp4"

    same_path.write_bytes(b"first video bytes")
    first = tr._cache_path_for(str(same_path))

    same_path.write_bytes(b"a completely different video")
    second = tr._cache_path_for(str(same_path))

    assert first != second, "same filename, different content, same cache key"


def test_the_same_video_keeps_its_cache(tmp_path):
    """Re-running on an unchanged source must still hit, or it re-bills every time."""
    v = tmp_path / "yt_input.mp4"
    v.write_bytes(b"identical bytes")
    assert tr._cache_path_for(str(v)) == tr._cache_path_for(str(v))


def test_the_key_still_names_the_video(tmp_path):
    """A directory of opaque hashes is unreadable when something goes wrong."""
    v = tmp_path / "yt_input.mp4"
    v.write_bytes(b"x")
    assert tr._cache_path_for(str(v)).name.startswith("yt_input_")


def test_an_unreadable_video_does_not_block_transcription(tmp_path):
    """The cache's own bookkeeping must not fail a caller that could proceed."""
    missing = tmp_path / "gone.mp4"
    assert tr._cache_path_for(str(missing)).name == "gone_transcript.txt"
