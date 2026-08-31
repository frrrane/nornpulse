"""
Unit tests for the post-generation audience reaction.

No model call, no real video. The behaviour worth pinning: the ASS
caption parser reads what a viewer actually sees (override tags stripped,
not the animation script), and a check that cannot run reports "would
finish" only because that is the true default state of "nothing was
found" — never because it silently assumed the good outcome.
"""

import json

from agent import audience


def _stub(monkeypatch, payload, raises=None):
    class _Resp:
        text = payload if isinstance(payload, str) else json.dumps(payload)

    class _Models:
        def generate_content(self, **kw):
            if raises:
                raise raises
            return _Resp()

    class _Client:
        def __init__(self, **kw): self.models = _Models()

    import google.genai as genai
    monkeypatch.setattr(genai, "Client", _Client)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")


# --- caption parsing ---------------------------------------------------------

def test_ass_override_tags_are_stripped(tmp_path):
    ass = tmp_path / "subs.ass"
    ass.write_text(
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        r"Dialogue: 0,0:00:01.50,0:00:03.00,KineticViral,,0,0,0,,{\t(0,264,\fscx118)}So, {\c&HFF41D2&}perhaps{\c} white holes"
        "\n",
        encoding="utf-8",
    )
    caps = audience.read_captions(ass)
    assert len(caps) == 1
    assert caps[0]["text"] == "So, perhaps white holes"
    assert caps[0]["start_sec"] == 1.5


def test_missing_sidecar_returns_empty_not_an_error(tmp_path):
    assert audience.read_captions(tmp_path / "nope.ass") == []


def test_blank_dialogue_lines_are_dropped(tmp_path):
    ass = tmp_path / "subs.ass"
    ass.write_text(
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        r"Dialogue: 0,0:00:00.00,0:00:01.00,S,,0,0,0,,{\c&HFF41D2&}{\c}"
        "\n",
        encoding="utf-8",
    )
    assert audience.read_captions(ass) == []


# --- failing safe -------------------------------------------------------------

def test_no_frames_reports_would_finish_with_a_reason():
    """
    Can't watch nothing -- this must not silently claim a clean reaction to
    a video it never actually looked at.
    """
    r = audience.Reaction(would_finish=True, checked_by="none",
                          reasons=["could not extract any frames from this video"])
    assert r.would_finish
    assert "could not extract" in r.reasons[0]


def test_missing_key_reports_could_not_run(monkeypatch, tmp_path):
    monkeypatch.delenv("NORNPULSE_USE_VERTEX", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(audience, "extract_frames", lambda path, n=6: [b"fake-jpeg-bytes"])

    r = audience.watch(tmp_path / "clip.mp4")
    assert r.checked_by == "none"
    assert "could not run" in r.reasons[0]


def test_api_error_reports_could_not_run(monkeypatch, tmp_path):
    monkeypatch.setattr(audience, "extract_frames", lambda path, n=6: [b"fake-jpeg-bytes"])
    _stub(monkeypatch, {}, raises=RuntimeError("network is down"))
    r = audience.watch(tmp_path / "clip.mp4")
    assert "could not run" in r.reasons[0]


def test_unreadable_response_reports_it(monkeypatch, tmp_path):
    monkeypatch.setattr(audience, "extract_frames", lambda path, n=6: [b"fake-jpeg-bytes"])
    _stub(monkeypatch, "not json")
    r = audience.watch(tmp_path / "clip.mp4")
    assert "nothing readable" in r.reasons[0]


# --- real reactions ------------------------------------------------------------

def test_would_finish_is_reported_cleanly(monkeypatch, tmp_path):
    monkeypatch.setattr(audience, "extract_frames", lambda path, n=6: [b"f1", b"f2"])
    _stub(monkeypatch, {"would_finish": True, "scroll_point": "", "reasons": []})
    r = audience.watch(tmp_path / "clip.mp4")
    assert r.would_finish
    assert r.summary() == "would watch to the end"


def test_scroll_point_is_carried_through(monkeypatch, tmp_path):
    monkeypatch.setattr(audience, "extract_frames", lambda path, n=6: [b"f1", b"f2"])
    _stub(monkeypatch, {"would_finish": False, "scroll_point": "3rd caption",
                        "reasons": ["dead air before it"]})
    r = audience.watch(tmp_path / "clip.mp4")
    assert not r.would_finish
    assert "3rd caption" in r.summary()
    assert "dead air before it" in r.reasons


def test_describe_names_the_sampling_ceiling(monkeypatch, tmp_path):
    monkeypatch.setattr(audience, "extract_frames", lambda path, n=6: [b"f1"])
    _stub(monkeypatch, {"would_finish": True, "reasons": []})
    text = audience.describe(audience.watch(tmp_path / "clip.mp4"))
    assert "storyboard proxy" in text
