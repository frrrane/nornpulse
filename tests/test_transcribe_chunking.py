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
