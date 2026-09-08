"""
Unit tests for scheduled staging's own safety logic.

Nothing here actually runs trend_publish.py — subprocess.run is
monkeypatched to a canned CompletedProcess, since a real invocation would
generate a real Veo clip and cost real money. What's worth guarding is the
daily-cap bookkeeping (a bug there is a bug that either silently stops
staging, or silently removes the one thing standing between this and an
unbounded generation loop) and the output-marker classification (a
regression in trend_publish.py's own printed text would otherwise make
every future run look like "no topic" or "failed" without anything
actually breaking).
"""

import subprocess
from datetime import date, timedelta

import pytest

from agent import norn_cron as nc


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "state.json"


def _fake_completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                        stdout=stdout, stderr="")


# --------------------------------------------------------------------------
# Daily cap bookkeeping
# --------------------------------------------------------------------------

def test_fresh_state_counts_as_zero_today(state_path):
    assert nc._today_count("nornpulse", state_path) == 0


def test_record_staged_increments_within_the_same_day(state_path):
    nc._record_staged("nornpulse", state_path)
    nc._record_staged("nornpulse", state_path)
    assert nc._today_count("nornpulse", state_path) == 2


def test_a_new_day_resets_the_count(state_path):
    state_path.write_text(
        '{"nornpulse": {"date": "2020-01-01", "count": 2}}', encoding="utf-8")
    assert nc._today_count("nornpulse", state_path) == 0


def test_channels_are_tracked_independently(state_path):
    nc._record_staged("nornpulse", state_path)
    assert nc._today_count("sloptokdaily", state_path) == 0
    assert nc._today_count("nornpulse", state_path) == 1


def test_corrupt_state_file_is_treated_as_empty_rather_than_raising(state_path):
    state_path.write_text("not json", encoding="utf-8")
    assert nc._today_count("nornpulse", state_path) == 0


# --------------------------------------------------------------------------
# stage_one: cap enforcement and output classification
# --------------------------------------------------------------------------

def test_stage_one_skips_once_the_cap_is_reached(state_path, monkeypatch):
    nc._record_staged("nornpulse", state_path)
    nc._record_staged("nornpulse", state_path)
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: called.append(1))
    result = nc.stage_one("nornpulse", max_per_day=2, state_path=state_path)
    assert result == "skipped_cap"
    assert called == []  # trend_publish.py must not even be invoked


def test_stage_one_recognises_a_successful_staging(state_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _fake_completed(
        "...\n✅ sent. Reply APPROVE or REJECT, then run:\n..."))
    result = nc.stage_one("nornpulse", max_per_day=2, state_path=state_path)
    assert result == "staged"
    assert nc._today_count("nornpulse", state_path) == 1


def test_stage_one_recognises_no_suitable_topic_as_not_a_failure(state_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _fake_completed(
        "🤷 Nothing trending suits this channel right now, so nothing was made."))
    result = nc.stage_one("nornpulse", max_per_day=2, state_path=state_path)
    assert result == "no_topic"
    # A free, expected outcome must not spend the daily cap.
    assert nc._today_count("nornpulse", state_path) == 0


def test_stage_one_reports_failed_on_an_unrecognised_outcome(state_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _fake_completed(
        "Traceback (most recent call last):\n...", returncode=1))
    result = nc.stage_one("nornpulse", max_per_day=2, state_path=state_path)
    assert result == "failed"
    assert nc._today_count("nornpulse", state_path) == 0


def test_stage_one_reports_failed_on_timeout(state_path, monkeypatch):
    def _timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="trend_publish.py", timeout=1)
    monkeypatch.setattr(subprocess, "run", _timeout)
    result = nc.stage_one("nornpulse", max_per_day=2, state_path=state_path,
                          timeout_sec=1)
    assert result == "failed"


def test_stage_one_invokes_generate_and_stage_flags(state_path, monkeypatch):
    """
    The whole point of this module: it must call trend_publish.py's
    reviewed, sanctioned path (--generate --stage), never --publish.
    """
    captured = {}

    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _fake_completed("✅ sent. Reply APPROVE or REJECT")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    nc.stage_one("nornpulse", max_per_day=2, state_path=state_path)
    assert "--generate" in captured["cmd"]
    assert "--stage" in captured["cmd"]
    assert "--publish" not in captured["cmd"]


# --------------------------------------------------------------------------
# main(): exit code reflects real failures, not skips or no-topic runs
# --------------------------------------------------------------------------

def test_main_exits_zero_when_nothing_actually_failed(state_path, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _fake_completed(
        "🤷 Nothing trending suits this channel right now, so nothing was made."))
    assert nc.main(["--channel", "nornpulse"]) == 0


def test_main_exits_nonzero_when_a_channel_fails(state_path, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _fake_completed(
        "boom", returncode=1))
    assert nc.main(["--channel", "nornpulse"]) == 1
