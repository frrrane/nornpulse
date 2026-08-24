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
