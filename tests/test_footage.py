"""
Unit tests for footage sourcing.

No video is generated and no file is downloaded. The properties worth
guarding are the licence checks, because getting one wrong publishes
material the channel has no right to use — and unlike a rendering bug, that
failure is silent until it is a strike.
"""

import pytest

from agent import footage as fg


# --- licence gating --------------------------------------------------------

@pytest.mark.parametrize("licence", [
    "CC0", "cc0", "Public domain", "PD-old-100", "CC BY 4.0",
    "CC BY-SA 3.0", "cc-by-sa",
])
def test_free_licences_are_accepted(licence):
    assert fg._licence_ok(licence)


@pytest.mark.parametrize("licence", [
    None, "", "All rights reserved", "CC BY-NC 4.0", "Fair use",
    "Copyrighted free use with permission", "unknown",
])
def test_anything_not_recognisably_free_is_refused(licence):
    """
    An unrecognised licence string is not permission. Commons hosts material
    under many licences and "it was on Commons" is not one of them.
    """
    assert not fg._licence_ok(licence)


def test_noncommercial_is_refused_despite_containing_cc_by():
    """
    CC BY-NC contains the substring "cc by" but forbids the commercial use a
    monetisable channel implies. A naive substring check would let it pass.
    """
    assert not fg._licence_ok("CC BY-NC-ND 4.0")


# --- attribution -----------------------------------------------------------

def test_attribution_required_for_by_licences(tmp_path):
    shot = fg.Footage(path=tmp_path / "a.mp4", source="public_domain",
                      provider="wikimedia", description="x", licence="CC BY 4.0")
    assert shot.needs_attribution


def test_public_domain_needs_no_attribution(tmp_path):
    shot = fg.Footage(path=tmp_path / "a.mp4", source="public_domain",
                      provider="wikimedia", description="x", licence="CC0")
    assert not shot.needs_attribution


def test_generated_footage_needs_no_attribution(tmp_path):
    shot = fg.Footage(path=tmp_path / "a.mp4", source="generated",
                      provider=fg.VEO_FAST, description="a prompt",
                      licence="generated")
    assert not shot.needs_attribution


# --- the source interface --------------------------------------------------

def test_unknown_source_is_rejected_rather_than_defaulted(tmp_path):
    """
    Silently falling back to generation would spend money the caller did not
    ask to spend; falling back to an archive would publish frames they did
    not choose.
    """
    with pytest.raises(ValueError, match="Unknown footage source"):
        fg.obtain("a prompt", tmp_path / "out.mp4", prefer="scrape_youtube")


def test_generation_without_a_key_fails_before_calling_out(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(fg.FootageError, match="GEMINI_API_KEY"):
        fg.generate_with_veo("a prompt", tmp_path / "out.mp4")


def test_default_model_is_not_the_most_expensive_variant():
    """An unattended loop should not default to the priciest option."""
    assert fg.DEFAULT_VEO_MODEL != fg.VEO_FULL


def test_no_hardcoded_price_is_quoted():
    """
    Veo's per-second rate changes and differs per variant. A stale number in
    the code would be quoted at the operator as though it were current.
    """
    import inspect
    import re
    src = inspect.getsource(fg)
    assert "VEO_PRICING_URL" in src
    # A dollar amount, not any "$" — regex anchors contain one legitimately.
    assert not re.search(r"\$\s*\d", src)


def test_generate_audio_is_not_sent_by_default():
    """
    The SDK exposes generate_audio, but the Gemini Developer API — the
    API-key path this project uses — rejects the whole request when it is
    present. Veo 3.x produces audio there by default anyway.
    """
    import inspect
    src = inspect.getsource(fg.generate_with_veo)
    assert "generate_audio: Optional[bool] = None" in src
    assert "if generate_audio is not None:" in src


# --- telling failures apart ------------------------------------------------

@pytest.mark.parametrize("code", sorted(fg.TRANSIENT_ERROR_CODES))
def test_backend_fault_codes_are_known_transient(code):
    assert code in fg.TRANSIENT_ERROR_CODES


def test_the_real_backend_message_reads_as_transient():
    """
    The exact text Veo returned in production. It was being reported as
    "the prompt was refused", which sends someone to rewrite a prompt that
    was never the problem — and costs them the next generation too.
    """
    assert fg._looks_transient(
        "Video generation failed due to an internal server issue. "
        "Please try again in a few minutes.")


@pytest.mark.parametrize("message", [
    "the prompt was blocked by safety filters",
    "invalid argument",
    "",
])
def test_genuine_refusals_are_not_treated_as_transient(message):
    assert not fg._looks_transient(message)


# --- saving the result -----------------------------------------------------
#
# The surfaces return finished video differently, and getting this wrong is
# the most expensive possible bug in this module: the clip is generated and
# billed, then discarded at the final step. It happened -- a Vertex run
# produced a video and then failed with "This method is only supported in
# the Gemini Developer client", because the save path went through the
# Files API, which Vertex does not have.

class _Video:
    def __init__(self, video_bytes=None):
        self.video_bytes = video_bytes
        self.saved_to = None

    def save(self, path):
        self.saved_to = path
        import pathlib
        pathlib.Path(path).write_bytes(b"downloaded-bytes")


def _fake_operation(video):
    class _Resp:
        generated_videos = [type("G", (), {"video": video})()]

    return type("Op", (), {"done": True, "response": _Resp(), "error": None})()


def _client_for(video, files_download_raises=None):
    calls = {"download": 0}

    class _Files:
        def download(self, file):
            calls["download"] += 1
            if files_download_raises:
                raise files_download_raises

    class _Models:
        def generate_videos(self, **kw):
            return _fake_operation(video)

    class _Client:
        def __init__(self):
            self.files = _Files()
            self.models = _Models()
            self.operations = type("O", (), {"get": staticmethod(lambda op: op)})()

    return _Client(), calls


def _run(monkeypatch, tmp_path, video, **kw):
    client, calls = _client_for(video, **kw)
    monkeypatch.setattr(
        "agent.genai_client.client_for",
        lambda model, api_key=None: (client, "veo-3.1-fast-generate-001"))
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    out = tmp_path / "clip.mp4"
    result = fg.generate_with_veo("a prompt", out, duration_sec=8)
    return result, out, calls


def test_inline_bytes_are_written_without_touching_the_files_api(monkeypatch, tmp_path):
    """The Vertex path. Files API here would raise, as it did in production."""
    video = _Video(video_bytes=b"vertex-inline-bytes")
    result, out, calls = _run(monkeypatch, tmp_path, video)
    assert out.read_bytes() == b"vertex-inline-bytes"
    assert calls["download"] == 0
    assert result.path == out


def test_a_handle_still_goes_through_the_files_api(monkeypatch, tmp_path):
    """The AI Studio path must keep working."""
    video = _Video(video_bytes=None)
    _, out, calls = _run(monkeypatch, tmp_path, video)
    assert calls["download"] == 1
    assert out.read_bytes() == b"downloaded-bytes"


def test_a_failed_save_says_the_video_was_generated(monkeypatch, tmp_path):
    """
    The distinction is worth money: a generation that was made and lost is
    a different problem from one that never happened, and the message has
    to say which, or the reader retries the wrong thing.
    """
    video = _Video(video_bytes=None)
    with pytest.raises(fg.FootageError, match="generated but could not be saved"):
        _run(monkeypatch, tmp_path, video,
             files_download_raises=ValueError("only supported in the Gemini Developer client"))
