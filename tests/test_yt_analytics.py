"""
Unit tests for the owner-only analytics layer.

No API is called. The behaviour that matters is the distinction between
"too few viewers to report on" and "zero" — YouTube returns an empty row
set for the first, and for a channel averaging a few hundred views that is
the expected outcome rather than a failure. Reporting it as zero retention
would be inventing a finding.
"""

import pytest

from agent import yt_analytics as ya


class _FakeReports:
    def __init__(self, response, raises=None):
        self._response = response
        self._raises = raises
        self.params = {}

    def query(self, **kw):
        self.params = kw
        if self._raises:
            raise self._raises
        return type("R", (), {"execute": lambda s: self._response})()


class _FakeClient:
    def __init__(self, response, raises=None):
        self._reports = _FakeReports(response, raises)

    def reports(self):
        return self._reports


def _install(monkeypatch, response, raises=None):
    client = _FakeClient(response, raises)
    monkeypatch.setattr(ya, "_client", lambda creds: client)
    return client


def _curve(points):
    return {
        "columnHeaders": [{"name": "elapsedVideoTimeRatio"},
                          {"name": "audienceWatchRatio"},
                          {"name": "relativeRetentionPerformance"}],
        "rows": [[ratio, watch, 0.5] for ratio, watch in points],
    }


# --- empty is an answer, not a zero ----------------------------------------

def test_an_empty_report_is_none_not_zero(monkeypatch):
    _install(monkeypatch, {"rows": []})
    assert ya.summary(None, "2026-08-01", "2026-08-25", "vid") is None


def test_an_empty_curve_is_empty_not_flat(monkeypatch):
    _install(monkeypatch, {"rows": []})
    assert ya.retention_curve(None, "vid", "2026-08-01", "2026-08-25") == []


def test_diagnose_says_why_there_is_nothing(monkeypatch):
    """
    "Not reportable" and "nobody watched" are different, and a channel at
    this size will hit the first constantly.
    """
    _install(monkeypatch, {"rows": []})
    result = ya.diagnose(None, "vid", "2026-08-01", "2026-08-25")
    assert result["reportable"] is False
    assert any("too few viewers" in f for f in result["findings"])
    assert "hook_retention" not in result


# --- the scope problem is named ---------------------------------------------

def test_a_missing_scope_says_re_authorisation_is_required(monkeypatch):
    _install(monkeypatch, {},
             raises=Exception("403 ... insufficient authentication scopes"))
    with pytest.raises(ya.AnalyticsUnavailable, match="Re-run the OAuth flow"):
        ya.summary(None, "2026-08-01", "2026-08-25")


def test_a_disabled_api_is_not_reported_as_a_scope_problem(monkeypatch):
    """
    Both failures are 403s with completely different fixes, and the first
    version of this matched on the status alone -- so a correctly-scoped
    token was told to re-authorise when the real problem was an API that
    had never been switched on. Re-authorising would not have helped, and
    the message would have sent someone to do it twice.
    """
    _install(monkeypatch, {}, raises=Exception(
        '403 ... "YouTube Analytics API has not been used in project 6474763116 '
        'before or it is disabled."'))
    with pytest.raises(ya.AnalyticsUnavailable) as excinfo:
        ya.summary(None, "2026-08-01", "2026-08-25")
    assert "not enabled" in str(excinfo.value)
    assert "OAuth" not in str(excinfo.value)


def test_an_unrecognised_failure_is_passed_through_verbatim(monkeypatch):
    """Better no diagnosis than a confident wrong one."""
    _install(monkeypatch, {}, raises=Exception("503 backend is having a day"))
    with pytest.raises(ya.AnalyticsUnavailable, match="backend is having a day"):
        ya.summary(None, "2026-08-01", "2026-08-25")


def test_diagnose_reports_a_scope_failure_rather_than_raising(monkeypatch):
    _install(monkeypatch, {}, raises=Exception("403 insufficient authentication scopes"))
    result = ya.diagnose(None, "vid", "2026-08-01", "2026-08-25")
    assert result["reportable"] is False
    assert any("could not be read" in f for f in result["findings"])


def test_the_analytics_scope_is_requested_at_grant_time():
    """A scope not asked for at grant time can never be added later."""
    from agent.norn_publisher import SCOPES
    assert ya.ANALYTICS_SCOPE in SCOPES


# --- reading the curve ------------------------------------------------------

def test_hook_retention_reads_the_end_of_the_hook_window(monkeypatch):
    curve = [{"elapsedVideoTimeRatio": r, "audienceWatchRatio": w}
             for r, w in [(0.0, 1.0), (0.05, 0.8), (0.15, 0.4), (0.5, 0.3)]]
    assert ya.hook_retention(curve) == 0.4


def test_hook_retention_of_an_empty_curve_is_none():
    assert ya.hook_retention([]) is None


def test_a_collapsing_hook_is_named_as_the_problem(monkeypatch):
    _install(monkeypatch, _curve([(0.0, 1.0), (0.1, 0.3)]))
    result = ya.diagnose(None, "vid", "2026-08-01", "2026-08-25")
    assert result["reportable"]
    assert any("the opening is the problem" in f for f in result["findings"])


def test_a_hook_that_holds_points_elsewhere(monkeypatch):
    _install(monkeypatch, _curve([(0.0, 1.0), (0.1, 0.9)]))
    result = ya.diagnose(None, "vid", "2026-08-01", "2026-08-25")
    assert any("happened later" in f for f in result["findings"])


def test_the_query_is_scoped_to_the_video(monkeypatch):
    client = _install(monkeypatch, _curve([(0.0, 1.0)]))
    ya.retention_curve(None, "abc123", "2026-08-01", "2026-08-25")
    assert client.reports().params["filters"] == "video==abc123"
    assert client.reports().params["dimensions"] == ya.RETENTION_DIMENSION


# --- a row of zeros is not a measurement ------------------------------------

def test_zero_views_is_not_reported_as_a_measurement(monkeypatch):
    """
    YouTube returns a populated row of zeros for a video too new to have
    been processed -- its analytics lag publication by a day or more. Read
    literally that becomes "the average viewer watched 0% of it" about a
    video nobody has had the chance to watch, which is the fabricated
    finding this whole module exists to refuse. Observed on a clip an hour
    after publishing.
    """
    _install(monkeypatch, {
        "columnHeaders": [{"name": "views"}, {"name": "averageViewPercentage"}],
        "rows": [[0, 0]],
    })
    assert ya.summary(None, "2026-08-25", "2026-08-26", "vid") is None


def test_a_real_row_still_comes_back(monkeypatch):
    _install(monkeypatch, {
        "columnHeaders": [{"name": "views"}, {"name": "averageViewPercentage"}],
        "rows": [[48, 69.08]],
    })
    assert ya.summary(None, "2026-08-19", "2026-08-26")["views"] == 48


def test_diagnose_says_it_may_simply_be_too_soon(monkeypatch):
    """
    "Too few viewers" and "published an hour ago" are different situations
    with the same empty result, and the message should not pick one.
    """
    _install(monkeypatch, {"rows": []})
    findings = " ".join(ya.diagnose(None, "vid", "2026-08-25", "2026-08-26")["findings"])
    assert "too recently published" in findings
    assert "measurement of zero" in findings
