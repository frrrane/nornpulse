"""
Unit tests for the pre-flight rights check.

No model is called. The properties worth guarding are the ones where a
wrong answer is expensive in opposite directions: passing something that
infringes, and blocking a knight for being a knight.

The most important behaviour here is what happens when the check cannot
run. A guard that reports "clean" because it failed is worse than no guard,
because the caller then has a verdict they believe.
"""

import pytest

from agent import watchdog as wd


def _stub(monkeypatch, payload, raises=None):
    class _Resp:
        text = payload if isinstance(payload, str) else __import__("json").dumps(payload)

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


# --- failing safe ----------------------------------------------------------

def test_missing_key_flags_rather_than_passing(monkeypatch):
    """
    "Could not check" is not "nothing found". Reporting a clean result the
    check never obtained is the one failure that makes a guard harmful.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    v = wd.check_text(title="anything at all")
    assert v.level == wd.FLAG
    assert v.checked_by == "none"
    assert "could not run" in v.summary()


def test_api_error_flags(monkeypatch):
    _stub(monkeypatch, {}, raises=RuntimeError("network is down"))
    v = wd.check_text(title="anything")
    assert v.level == wd.FLAG
    assert not v.blocked


def test_unreadable_response_flags(monkeypatch):
    _stub(monkeypatch, "I cannot help with that request.")
    assert wd.check_text(title="anything").level == wd.FLAG


def test_unknown_verdict_flags(monkeypatch):
    _stub(monkeypatch, {"verdict": "probably fine", "reasons": []})
    assert wd.check_text(title="anything").level == wd.FLAG


# --- the deterministic net -------------------------------------------------

@pytest.mark.parametrize("text", [
    "Peter Griffin Paraguay Dance Fail",
    "The Sopurranos but it's the SOPRANOS intro",
    "JD Vance goes full hoedown",
    "a minion holds a ketchup bottle",
])
def test_known_property_is_blocked_without_a_model_call(text, monkeypatch):
    """
    Cheap, and it cannot be talked out of a finding by the material it is
    reading — which a model checking prose can be.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    v = wd.check_text(title=text)
    assert v.blocked
    assert v.checked_by == "pattern list"


def test_pattern_net_runs_before_the_model(monkeypatch):
    called = {"n": 0}

    class _Models:
        def generate_content(self, **kw):
            called["n"] += 1
            raise AssertionError("should not have been reached")

    class _Client:
        def __init__(self, **kw): self.models = _Models()

    import google.genai as genai
    monkeypatch.setattr(genai, "Client", _Client)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert wd.check_text(prompt="shrek in a swamp").blocked
    assert called["n"] == 0


def test_substring_matches_do_not_fire():
    """"alf" must not match "half", or every other brief trips the net."""
    assert not wd._HIGH_RISK.search("half a sandwich and a calf")


# --- verdicts --------------------------------------------------------------

def test_pass_is_reported_cleanly(monkeypatch):
    _stub(monkeypatch, {"verdict": "pass", "reasons": []})
    v = wd.check_text(title="A knight flips corndogs for an alligator")
    assert v.level == wd.PASS
    assert not v.blocked and not v.needs_human


def test_flag_needs_a_human_but_does_not_block(monkeypatch):
    _stub(monkeypatch, {"verdict": "flag", "reasons": ["evokes a specific look"]})
    v = wd.check_text(title="borderline")
    assert v.needs_human and not v.blocked


def test_block_carries_its_reasons(monkeypatch):
    _stub(monkeypatch, {"verdict": "block",
                        "reasons": ["depicts a specific animated character"]})
    v = wd.check_text(title="a suburban dad with a talking dog")
    assert v.blocked
    assert "animated character" in v.summary()


# --- honesty about scope ---------------------------------------------------

def test_every_verdict_states_what_was_not_checked(monkeypatch):
    """
    The gap has to be visible rather than implied — this check cannot look
    at footage, cannot inspect uploads, and cannot decide fair use.
    """
    _stub(monkeypatch, {"verdict": "pass", "reasons": []})
    v = wd.check_text(title="fine")
    joined = " ".join(v.checks_not_run)
    assert "resembles a protected work" in joined
    assert "uploaded video" in joined
    assert "fair use" in joined


def test_describe_shows_both_halves(monkeypatch):
    _stub(monkeypatch, {"verdict": "pass", "reasons": []})
    text = wd.describe(wd.check_text(title="fine"))
    assert "checked:" in text and "NOT checked:" in text


def test_clip_metadata_is_checked_not_just_the_prompt(monkeypatch):
    """
    A title reproducing a property is a claim whether or not a frame does.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    v = wd.check_clip({"hook_title": "Sopranos intro but cats", "tags": []})
    assert v.blocked
