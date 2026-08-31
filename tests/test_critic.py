"""
Unit tests for the pre-generation quality critic.

No model is called. The behaviour worth pinning is the same shape as
watchdog's: a check that cannot run must fail to REVISE, never to PASS,
because a critique that reports "fine" without having actually run is
worse than no critique — the caller believes a verdict nobody made.
"""

import json

import pytest

from agent import critic


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


class _Brief:
    def __init__(self, **kw):
        self.title = kw.get("title", "")
        self.caption = kw.get("caption", "")
        self.angle = kw.get("angle", "")
        self.hook_type = kw.get("hook_type", "")
        self.video_prompt = kw.get("video_prompt", "")


# --- failing safe, to REVISE not PASS --------------------------------------

def test_missing_key_revises_rather_than_passing(monkeypatch):
    monkeypatch.delenv("NORNPULSE_USE_VERTEX", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    v = critic.check_brief(_Brief(title="anything"), history=[])
    assert v.level == critic.REVISE
    assert v.checked_by == "none"
    assert "could not run" in v.summary()


def test_api_error_revises(monkeypatch):
    _stub(monkeypatch, {}, raises=RuntimeError("network is down"))
    v = critic.check_brief(_Brief(title="anything"), history=[])
    assert v.level == critic.REVISE
    assert not v.blocked


def test_unreadable_response_revises(monkeypatch):
    _stub(monkeypatch, "I cannot help with that request.")
    assert critic.check_brief(_Brief(title="x"), history=[]).level == critic.REVISE


def test_unknown_verdict_revises(monkeypatch):
    _stub(monkeypatch, {"verdict": "meh", "reasons": []})
    assert critic.check_brief(_Brief(title="x"), history=[]).level == critic.REVISE


# --- verdicts ----------------------------------------------------------------

def test_pass_is_reported_cleanly(monkeypatch):
    _stub(monkeypatch, {"verdict": "pass", "reasons": [], "scroll_risk": ""})
    v = critic.check_brief(_Brief(title="fine"), history=[])
    assert v.level == critic.PASS
    assert not v.needs_revision


def test_revise_needs_revision_but_is_not_blocked(monkeypatch):
    _stub(monkeypatch, {"verdict": "revise", "scroll_risk": "held-pose ending",
                        "reasons": ["matches a past rejection"]})
    v = critic.check_brief(_Brief(title="x"), history=[])
    assert v.needs_revision and not v.blocked
    assert "held-pose ending" in v.summary()


def test_block_is_blocked(monkeypatch):
    _stub(monkeypatch, {"verdict": "block", "scroll_risk": "premise cannot fit 8s",
                        "reasons": []})
    v = critic.check_brief(_Brief(title="x"), history=[])
    assert v.blocked and v.needs_revision


# --- rejection history --------------------------------------------------------

def test_history_is_shown_to_the_model(monkeypatch):
    """The prompt actually carries real rejection comments, not just a count."""
    seen = {}

    class _Resp:
        text = '{"verdict": "pass", "reasons": []}'

    class _Models:
        def generate_content(self, **kw):
            seen["contents"] = kw.get("contents", "")
            return _Resp()

    class _Client:
        def __init__(self, **kw): self.models = _Models()

    import google.genai as genai
    monkeypatch.setattr(genai, "Client", _Client)
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    history = [{"clip_id": "c1", "comment": "way too bouncy"}]
    critic.check_brief(_Brief(title="x"), history=history)
    assert "way too bouncy" in seen["contents"]


def test_empty_history_does_not_crash(monkeypatch):
    _stub(monkeypatch, {"verdict": "pass", "reasons": []})
    v = critic.check_brief(_Brief(title="x"), history=[])
    assert v.history_sample == 0


def test_rejection_history_reads_the_real_ledger(monkeypatch):
    """Pulls from review_queue, not a hardcoded list, so it stays current."""
    from agent import review_queue as rq

    def _fake_list_decisions(status=None, path=None):
        assert status == rq.REJECTED
        return [{"clip_id": "a", "comment": "too bouncy"},
                {"clip_id": "b", "comment": ""}]  # blank comments are dropped

    monkeypatch.setattr(rq, "list_decisions", _fake_list_decisions)
    hist = critic.rejection_history()
    assert hist == [{"clip_id": "a", "comment": "too bouncy"}]


# --- one revision loop ---------------------------------------------------------

def test_one_revision_is_attempted_when_needed(monkeypatch):
    calls = {"n": 0}

    def fake_write_brief(channel, topics, **kw):
        calls["n"] += 1
        return _Brief(title=f"attempt {calls['n']}")

    verdicts = iter([
        {"verdict": "revise", "scroll_risk": "x", "reasons": []},
        {"verdict": "pass", "reasons": []},
    ])
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")

    class _Models:
        def generate_content(self, **kw):
            class _Resp:
                text = json.dumps(next(verdicts))
            return _Resp()

    class _Client:
        def __init__(self, **kw): self.models = _Models()

    import google.genai as genai
    monkeypatch.setattr(genai, "Client", _Client)
    monkeypatch.setattr(critic, "rejection_history", lambda limit=critic.HISTORY_LIMIT: [])

    brief, verdict = critic.critique_with_one_revision(
        channel=object(), topics=[], write_brief_fn=fake_write_brief)

    assert calls["n"] == 2  # first attempt + exactly one revision, not a loop
    assert brief.title == "attempt 2"
    assert verdict.level == critic.PASS


def test_no_revision_needed_writes_brief_once(monkeypatch):
    calls = {"n": 0}

    def fake_write_brief(channel, topics, **kw):
        calls["n"] += 1
        return _Brief(title="good on the first try")

    _stub(monkeypatch, {"verdict": "pass", "reasons": []})
    monkeypatch.setattr(critic, "rejection_history", lambda limit=critic.HISTORY_LIMIT: [])

    brief, verdict = critic.critique_with_one_revision(
        channel=object(), topics=[], write_brief_fn=fake_write_brief)
    assert calls["n"] == 1
    assert verdict.level == critic.PASS


def test_write_brief_declining_short_circuits(monkeypatch):
    """write_brief returning None ('nothing suits this channel') is a real
    answer, not something the critic should paper over by trying again."""
    def fake_write_brief(channel, topics, **kw):
        return None

    brief, verdict = critic.critique_with_one_revision(
        channel=object(), topics=[], write_brief_fn=fake_write_brief)
    assert brief is None and verdict is None
