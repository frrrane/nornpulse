"""
The preflight checklist, and the limits of it.

Every rejection recorded in this project is a mechanical craft defect —
"the start is cut off", "cuts of mid sentence", "too bouncy", "the
subtitles arent synced" — with one exception, "not funny at all", which no
checklist can find. These guard the checks, and the last one guards the
honesty of the report about what it does not cover.
"""

import pytest

from agent import preflight


TRANSCRIPT = (
    "[00:10.000] a sentence that begins the window\n"
    "[00:14.000] a second sentence in the middle\n"
    "[00:18.000] a third sentence that runs past the end\n"
    "[00:26.000] a fourth well clear of it\n"
)


def _clip(start, end, **extra):
    return {"clip_id": "t", "start_time": start, "end_time": end, **extra}


def test_a_clean_cut_is_clean():
    r = preflight.check_clip(_clip("00:10", "00:18"), TRANSCRIPT)
    assert r.clean, [str(f) for f in r.findings]


def test_a_cut_opening_mid_sentence_is_caught():
    r = preflight.check_clip(_clip("00:12", "00:18"), TRANSCRIPT)
    assert any(f.check == "start_mid_sentence" for f in r.findings)


def test_a_cut_closing_mid_sentence_is_caught():
    """The fault that got clip_cone_analogy rejected."""
    r = preflight.check_clip(_clip("00:10", "00:20"), TRANSCRIPT)
    assert any(f.check == "end_mid_sentence" for f in r.findings)


def test_a_finding_names_the_rejection_it_comes_from():
    r = preflight.check_clip(_clip("00:10", "00:20"), TRANSCRIPT)
    end = next(f for f in r.findings if f.check == "end_mid_sentence")
    assert end.because == "cuts of mid sentence"


@pytest.mark.parametrize("start,end,expected", [
    ("00:10", "00:26", "too_long"),
    ("00:10", "00:14", None),          # 4s is under the floor but so is the data
])
def test_duration_is_measured_against_what_travelled(start, end, expected):
    r = preflight.check_clip(_clip(start, end), TRANSCRIPT, min_sec=6.0, max_sec=10.0)
    checks = {f.check for f in r.findings}
    if expected:
        assert expected in checks
    else:
        assert "too_long" not in checks


def test_a_forbidden_treatment_is_caught():
    class P:
        avoid_motion = ["shake"]
        avoid_crop = ["blurred_background"]
    r = preflight.check_clip(
        _clip("00:10", "00:18", motion_effect="shake", crop_mode="center_crop"),
        TRANSCRIPT, profile=P())
    assert any(f.check == "forbidden_motion_effect" for f in r.findings)
    assert not any(f.check == "forbidden_crop_mode" for f in r.findings)


def test_no_transcript_means_the_boundary_checks_are_declared_unrun():
    """A clean report must never look broader than the checks behind it."""
    r = preflight.check_clip(_clip("00:10", "00:18"), transcript_text="")
    assert any("sentence boundaries" in s for s in r.not_checked)


def test_taste_is_always_declared_unchecked():
    """
    One clip was rejected as "not funny at all" and one approved as "could be
    funnier". Neither is findable here, and a report that did not say so
    would be claiming more than it can support.
    """
    r = preflight.check_clip(_clip("00:10", "00:18"), TRANSCRIPT)
    assert any("interesting" in s for s in r.not_checked)
    assert any("captions match the audio" in s for s in r.not_checked)
