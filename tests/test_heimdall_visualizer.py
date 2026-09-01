"""
Unit tests for Heimdall's image composition — the cover thumbnail and,
for skuld_renderer.py's generated_backdrop crop mode, the backdrop image.

No real image generation happens here: the Gemini client is mocked, and
the property worth guarding is that compose_thumbnail and
compose_backdrop ask for genuinely different things (a subject vs.
atmosphere with no competing subject) even though they share the same
generate-and-save mechanics.
"""

from pathlib import Path

from agent.heimdall_visualizer import HeimdallVisualizer


def _stub_client(monkeypatch, image_bytes=b"fake-jpeg-bytes", raises=None):
    class _Part:
        class inline_data:
            data = image_bytes

    class _Content:
        parts = [_Part()]

    class _Candidate:
        content = _Content()

    class _Resp:
        candidates = [_Candidate()]

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


_MUSIC = {"genre": "cinematic orchestral", "mood": "epic", "energy_level": 0.8}


# --- the two prompts ask for different things --------------------------------

def test_thumbnail_prompt_asks_for_a_subject():
    h = HeimdallVisualizer.__new__(HeimdallVisualizer)  # skip __init__'s client setup
    prompt = h._build_prompt("A Black Hole's Final Moments", "cinematic", "epic", 0.8).lower()
    assert "cover" in prompt


def test_backdrop_prompt_explicitly_avoids_a_subject():
    """
    The backdrop sits BEHIND the source footage (see
    skuld_renderer.generated_backdrop), so a strong subject of its own
    would compete with whatever is composited on top of it.
    """
    h = HeimdallVisualizer.__new__(HeimdallVisualizer)
    prompt = h._build_backdrop_prompt("A Black Hole's Final Moments", "cinematic", "epic", 0.8).lower()
    assert "no sharp central subject" in prompt
    assert "compete" in prompt


def test_both_prompts_forbid_text_and_logos():
    h = HeimdallVisualizer.__new__(HeimdallVisualizer)
    for prompt in (
        h._build_prompt("x", "genre", "mood", 0.5),
        h._build_backdrop_prompt("x", "genre", "mood", 0.5),
    ):
        lowered = prompt.lower()
        assert "no text" in lowered or "no readable text" in lowered
        assert "no logos" in lowered


# --- generation & saving -------------------------------------------------------

def test_compose_backdrop_saves_to_a_distinct_filename(tmp_path, monkeypatch):
    _stub_client(monkeypatch)
    h = HeimdallVisualizer()
    path = h.compose_backdrop("clip_1", "A Black Hole's Final Moments", _MUSIC, output_dir=tmp_path)
    assert path is not None
    assert path.endswith("clip_1_backdrop.jpg")
    assert Path(path).read_bytes() == b"fake-jpeg-bytes"


def test_compose_thumbnail_and_backdrop_never_collide(tmp_path, monkeypatch):
    """Both can be composed for the same clip_id without one overwriting the other."""
    _stub_client(monkeypatch)
    h = HeimdallVisualizer()
    thumb = h.compose_thumbnail("clip_1", "title", _MUSIC, output_dir=tmp_path)
    backdrop = h.compose_backdrop("clip_1", "title", _MUSIC, output_dir=tmp_path)
    assert thumb != backdrop
    assert Path(thumb).exists() and Path(backdrop).exists()


# --- failing safe: never crash the clip over a decoration ---------------------

def test_api_error_returns_none_not_raises(tmp_path, monkeypatch):
    _stub_client(monkeypatch, raises=RuntimeError("network is down"))
    h = HeimdallVisualizer()
    assert h.compose_backdrop("clip_1", "title", _MUSIC, output_dir=tmp_path) is None
    assert h.compose_thumbnail("clip_1", "title", _MUSIC, output_dir=tmp_path) is None


def test_no_image_in_response_returns_none(tmp_path, monkeypatch):
    class _Content:
        parts = []  # no inline_data anywhere

    class _Candidate:
        content = _Content()

    class _Resp:
        candidates = [_Candidate()]

    class _Models:
        def generate_content(self, **kw): return _Resp()

    class _Client:
        def __init__(self, **kw): self.models = _Models()

    import google.genai as genai
    monkeypatch.setattr(genai, "Client", _Client)
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    h = HeimdallVisualizer()
    assert h.compose_backdrop("clip_1", "title", _MUSIC, output_dir=tmp_path) is None
