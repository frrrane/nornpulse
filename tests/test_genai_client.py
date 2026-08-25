"""
Unit tests for choosing between AI Studio and Vertex.

No network. What matters is that the default is unchanged -- this module
was added mid-project and must not alter a single call until someone opts
in -- and that a model's Vertex name and region travel together, because
they differ per model and getting either wrong is a 404 at the point of
spending money.
"""

import pytest

from agent import genai_client as gc


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("NORNPULSE_USE_VERTEX", "NORNPULSE_VERTEX_PROJECT",
                "NORNPULSE_VERTEX_LOCATION", "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")


def _fake_genai(monkeypatch):
    """Capture how the client was constructed."""
    seen = {}

    class _Client:
        def __init__(self, **kw):
            seen.update(kw)

    import google.genai as genai
    monkeypatch.setattr(genai, "Client", _Client)
    return seen


# --- the default must not move ---------------------------------------------

def test_ai_studio_is_the_default():
    assert not gc.use_vertex()


def test_default_client_uses_the_api_key(monkeypatch):
    seen = _fake_genai(monkeypatch)
    _, model = gc.client_for("gemini-3.6-flash")
    assert seen == {"api_key": "test-key-not-real"}
    assert model == "gemini-3.6-flash", "the name must not be rewritten off Vertex"


def test_a_missing_key_is_still_an_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _fake_genai(monkeypatch)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        gc.client_for("gemini-3.6-flash")


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("", False), ("maybe", False),
])
def test_the_flag_is_read_strictly(value, expected, monkeypatch):
    """"maybe" must not silently redirect where the money comes from."""
    monkeypatch.setenv("NORNPULSE_USE_VERTEX", value)
    assert gc.use_vertex() is expected


# --- Vertex routing --------------------------------------------------------

def _vertex(monkeypatch, project="norn-labs"):
    monkeypatch.setenv("NORNPULSE_USE_VERTEX", "true")
    monkeypatch.setenv("NORNPULSE_VERTEX_PROJECT", project)
    return _fake_genai(monkeypatch)


def test_vertex_client_is_built_for_the_project(monkeypatch):
    seen = _vertex(monkeypatch)
    gc.client_for("gemini-3.6-flash")
    assert seen["vertexai"] is True
    assert seen["project"] == "norn-labs"


def test_gemini_is_routed_to_global(monkeypatch):
    """Verified by a real call: us-central1 returns 404 for this model."""
    seen = _vertex(monkeypatch)
    _, model = gc.client_for("gemini-3.6-flash")
    assert seen["location"] == gc.GLOBAL
    assert model == "gemini-3.6-flash"


def test_veo_is_renamed_and_sent_to_us_central(monkeypatch):
    """The one model whose name genuinely differs between the surfaces."""
    seen = _vertex(monkeypatch)
    _, model = gc.client_for("veo-3.1-fast-generate-preview")
    assert model == "veo-3.1-fast-generate-001"
    assert seen["location"] == gc.US_CENTRAL


def test_models_needing_different_regions_get_different_clients(monkeypatch):
    """
    The whole reason location travels with the model: no single region
    serves both, so one setting could not be right for both.
    """
    seen = _vertex(monkeypatch)
    gc.client_for("gemini-3.6-flash")
    gemini_location = seen["location"]
    gc.client_for("veo-3.1-fast-generate-preview")
    assert seen["location"] != gemini_location


def test_an_unknown_model_passes_through_rather_than_raising(monkeypatch):
    seen = _vertex(monkeypatch)
    _, model = gc.client_for("gemini-9.9-flash-imaginary")
    assert model == "gemini-9.9-flash-imaginary"
    assert seen["location"] == gc.DEFAULT_LOCATION


def test_location_can_be_overridden(monkeypatch):
    seen = _vertex(monkeypatch)
    monkeypatch.setenv("NORNPULSE_VERTEX_LOCATION", "europe-west4")
    gc.client_for("gemini-3.6-flash")
    assert seen["location"] == "europe-west4"


def test_vertex_without_a_project_fails_loudly(monkeypatch):
    monkeypatch.setenv("NORNPULSE_USE_VERTEX", "true")
    _fake_genai(monkeypatch)
    with pytest.raises(RuntimeError, match="no project is configured"):
        gc.client_for("gemini-3.6-flash")


def test_google_cloud_project_is_accepted_as_the_project(monkeypatch):
    monkeypatch.setenv("NORNPULSE_USE_VERTEX", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "norn-labs")
    seen = _fake_genai(monkeypatch)
    gc.client_for("gemini-3.6-flash")
    assert seen["project"] == "norn-labs"


# --- honesty about what is actually known ----------------------------------

def test_every_model_the_code_calls_has_a_route():
    """A missing entry means a silent fall back to a possibly wrong region."""
    from agent import bragi_composer, footage, heimdall_visualizer, mimir_narrator
    for model in (bragi_composer.LYRIA_MODEL,
                  heimdall_visualizer.IMAGE_MODEL,
                  mimir_narrator.TTS_MODEL,
                  footage.VEO_FAST, footage.VEO_LITE, footage.VEO_FULL,
                  "gemini-3.6-flash"):
        assert model in gc.VERTEX_ROUTES, f"no Vertex route for {model}"


def test_unverified_routes_are_marked_as_such():
    """
    Only routes confirmed by a real call may claim to be verified. The
    catalogue lists models in regions that 404 on use, so an unverified
    route is a guess and has to look like one.
    """
    # Confirmed by real calls that returned output.
    assert gc.VERTEX_ROUTES["gemini-3.6-flash"].verified
    assert gc.VERTEX_ROUTES["veo-3.1-fast-generate-preview"].verified
    assert gc.VERTEX_ROUTES["gemini-3-pro-image"].verified
    # Never called for real; the catalogue alone is not evidence. Bragi's
    # cache keeps satisfying its requests, so Lyria has still never been
    # reached on Vertex.
    assert not gc.VERTEX_ROUTES["lyria-3-clip-preview"].verified


def test_describe_names_the_billing_target(monkeypatch):
    assert "AI Studio" in gc.describe()
    monkeypatch.setenv("NORNPULSE_USE_VERTEX", "true")
    monkeypatch.setenv("NORNPULSE_VERTEX_PROJECT", "norn-labs")
    assert "Vertex" in gc.describe() and "norn-labs" in gc.describe()
