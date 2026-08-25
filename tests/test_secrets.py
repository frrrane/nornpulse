"""
Unit tests for loading secrets.

Nothing is fetched. The properties worth guarding are the ones where a
mistake is silent and expensive: replacing a credential a developer
deliberately set, so the app talks to a different database than they think;
and logging a value instead of a name.
"""

import logging

import pytest

from agent import secrets


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in secrets.SECRET_NAMES + [
            "NORNPULSE_USE_SECRET_MANAGER", "GOOGLE_CLOUD_PROJECT",
            "NORNPULSE_VERTEX_PROJECT"]:
        monkeypatch.delenv(name, raising=False)


class _Payload:
    def __init__(self, value): self.data = value.encode("utf-8")


class _Response:
    def __init__(self, value): self.payload = _Payload(value)


def _fake_client(monkeypatch, values, raises=None):
    """Stand in for the Secret Manager client, recording what was asked for."""
    asked = []

    class _Client:
        def access_secret_version(self, request):
            asked.append(request["name"])
            if raises:
                raise raises
            secret_id = request["name"].split("/secrets/")[1].split("/")[0]
            if secret_id not in values:
                raise RuntimeError("NOT_FOUND")
            return _Response(values[secret_id])

    import google.cloud.secretmanager as sm
    monkeypatch.setattr(sm, "SecretManagerServiceClient", lambda: _Client())
    return asked


# --- off by default ---------------------------------------------------------

def test_it_does_nothing_unless_asked():
    """
    A workstation with a .env should not call an API for values it already
    has, and a developer without credentials should not meet a wall of
    permission errors on startup.
    """
    assert not secrets.enabled()
    assert secrets.load_secrets() == {}


@pytest.mark.parametrize("flag,expected", [
    ("1", True), ("true", True), ("YES", True), ("on", True),
    ("0", False), ("", False), ("maybe", False),
])
def test_the_flag_is_read_strictly(flag, expected, monkeypatch):
    monkeypatch.setenv("NORNPULSE_USE_SECRET_MANAGER", flag)
    assert secrets.enabled() is expected


def test_no_project_is_a_warning_not_a_crash(monkeypatch, caplog):
    monkeypatch.setenv("NORNPULSE_USE_SECRET_MANAGER", "true")
    with caplog.at_level(logging.WARNING):
        assert secrets.load_secrets() == {}
    assert "no project" in caplog.text


# --- never overwrite --------------------------------------------------------

def test_an_existing_value_is_never_replaced(monkeypatch):
    """
    The dangerous one. Substituting a different credential than the one a
    developer exported is how an app quietly talks to the wrong database.
    """
    monkeypatch.setenv("NORNPULSE_USE_SECRET_MANAGER", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "norn-labs")
    monkeypatch.setenv("GEMINI_API_KEY", "the-one-I-set")
    asked = _fake_client(monkeypatch, {"gemini-api-key": "from-the-cloud"})

    loaded = secrets.load_secrets(["GEMINI_API_KEY"])

    import os
    assert os.environ["GEMINI_API_KEY"] == "the-one-I-set"
    assert loaded == {}
    assert asked == [], "it should not even ask for a secret it already has"


def test_a_missing_value_is_filled_in(monkeypatch):
    monkeypatch.setenv("NORNPULSE_USE_SECRET_MANAGER", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "norn-labs")
    _fake_client(monkeypatch, {"gemini-api-key": "from-the-cloud"})

    loaded = secrets.load_secrets(["GEMINI_API_KEY"])

    import os
    assert os.environ["GEMINI_API_KEY"] == "from-the-cloud"
    assert loaded == {"GEMINI_API_KEY": "gemini-api-key"}


# --- never leak -------------------------------------------------------------

def test_the_return_value_carries_ids_not_secrets(monkeypatch):
    monkeypatch.setenv("NORNPULSE_USE_SECRET_MANAGER", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "norn-labs")
    _fake_client(monkeypatch, {"gemini-api-key": "sk-do-not-log-me"})

    loaded = secrets.load_secrets(["GEMINI_API_KEY"])
    assert "sk-do-not-log-me" not in str(loaded)


def test_nothing_logs_a_value(monkeypatch, caplog):
    monkeypatch.setenv("NORNPULSE_USE_SECRET_MANAGER", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "norn-labs")
    _fake_client(monkeypatch, {"gemini-api-key": "sk-do-not-log-me"})

    with caplog.at_level(logging.DEBUG):
        secrets.load_secrets(["GEMINI_API_KEY"])
    assert "sk-do-not-log-me" not in caplog.text


def test_describe_names_the_source_and_no_values(monkeypatch):
    assert "environment" in secrets.describe()
    monkeypatch.setenv("NORNPULSE_USE_SECRET_MANAGER", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "norn-labs")
    monkeypatch.setenv("GEMINI_API_KEY", "sk-do-not-log-me")
    text = secrets.describe()
    assert "Secret Manager" in text and "norn-labs" in text
    assert "sk-do-not-log-me" not in text


# --- degrading --------------------------------------------------------------

def test_an_unavailable_secret_does_not_stop_the_others(monkeypatch):
    """
    Whatever needs the missing one fails with its own message naming what
    it wanted, which is more useful than this module guessing.
    """
    monkeypatch.setenv("NORNPULSE_USE_SECRET_MANAGER", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "norn-labs")
    _fake_client(monkeypatch, {"gemini-api-key": "present"})

    loaded = secrets.load_secrets(["GEMINI_API_KEY", "CLICKHOUSE_PASSWORD"])
    assert list(loaded) == ["GEMINI_API_KEY"]


def test_an_unreachable_service_falls_back_quietly(monkeypatch, caplog):
    monkeypatch.setenv("NORNPULSE_USE_SECRET_MANAGER", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "norn-labs")
    _fake_client(monkeypatch, {}, raises=RuntimeError("permission denied"))
    with caplog.at_level(logging.INFO):
        assert secrets.load_secrets(["GEMINI_API_KEY"]) == {}


def test_every_secret_has_an_id_mapped():
    """An unmapped name silently skips, which looks like a missing secret."""
    assert set(secrets.SECRET_NAMES) == set(secrets.SECRET_IDS)
