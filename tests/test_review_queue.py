"""
Unit tests for the human review ledger and the email-reply parser.

The ledger is the audit trail for every publish/reject decision, so the
cases that matter are the ones that lose or corrupt it silently: a
truncated write, a decision overwriting its own history, an archive that
half-moves a clip's files. The comment extractor is tested against real
reply shapes, since a quoted original would otherwise be recorded as the
reviewer's comment.
"""

import json

import pytest

from agent import review_queue as rq
from check_approvals import SUBJECT_RE, extract_comment
from agent.norn_publisher import NornPublisher


@pytest.fixture
def ledger(tmp_path):
    return tmp_path / "review_decisions.json"


@pytest.fixture
def clip_files(tmp_path):
    out = tmp_path / "output_clips"
    out.mkdir()
    for suffix in ("_9x16.mp4", "_subs.ass", "_thumb.jpg", "_metadata.json"):
        (out / f"clip_1{suffix}").write_text("x")
    return out


# --------------------------------------------------------------------------
# Recording decisions
# --------------------------------------------------------------------------

def test_decision_round_trips(ledger):
    rq.record_decision("clip_1", rq.REJECTED, "hook lands too late", source="email",
                       path=ledger, mirror_to_clickhouse=False)
    got = rq.get_decision("clip_1", path=ledger)
    assert got["status"] == rq.REJECTED
    assert got["comment"] == "hook lands too late"
    assert got["source"] == "email"
    assert got["decided_at"]


def test_an_unknown_status_is_rejected(ledger):
    """status reaches a LowCardinality ClickHouse column; it must be constrained."""
    with pytest.raises(rq.ReviewError, match="Unknown review status"):
        rq.record_decision("clip_1", "maybe", path=ledger, mirror_to_clickhouse=False)


def test_a_decision_needs_a_clip_id(ledger):
    with pytest.raises(rq.ReviewError):
        rq.record_decision("", rq.APPROVED, path=ledger, mirror_to_clickhouse=False)


def test_redeciding_preserves_the_prior_decision(ledger):
    """
    A clip rejected by email and then approved in the dashboard is a real
    sequence. Overwriting silently would leave no way to explain it.
    """
    rq.record_decision("clip_1", rq.REJECTED, "too slow", source="email",
                       path=ledger, mirror_to_clickhouse=False)
    rq.record_decision("clip_1", rq.APPROVED, "recut, good now", source="ui",
                       path=ledger, mirror_to_clickhouse=False)
    got = rq.get_decision("clip_1", path=ledger)
    assert got["status"] == rq.APPROVED
    assert got["previous"]["status"] == rq.REJECTED
    assert got["previous"]["comment"] == "too slow"


def test_history_does_not_nest_without_bound(ledger):
    for i in range(4):
        rq.record_decision("clip_1", rq.APPROVED, f"pass {i}",
                           path=ledger, mirror_to_clickhouse=False)
    got = rq.get_decision("clip_1", path=ledger)
    assert "previous" not in got["previous"]


def test_comment_is_optional_and_normalised(ledger):
    entry = rq.record_decision("clip_1", rq.APPROVED, "   ",
                               path=ledger, mirror_to_clickhouse=False)
    assert entry["comment"] == ""


def test_extra_fields_are_stored(ledger):
    entry = rq.record_decision("clip_1", rq.APPROVED, "ship it",
                               extra={"youtube_url": "https://youtu.be/x"},
                               path=ledger, mirror_to_clickhouse=False)
    assert entry["youtube_url"] == "https://youtu.be/x"


def test_list_decisions_filters_and_orders(ledger):
    for cid, status in (("clip_1", rq.APPROVED), ("clip_2", rq.REJECTED), ("clip_3", rq.REJECTED)):
        rq.record_decision(cid, status, path=ledger, mirror_to_clickhouse=False)
    assert {r["clip_id"] for r in rq.list_decisions(rq.REJECTED, path=ledger)} == {"clip_2", "clip_3"}
    assert len(rq.list_decisions(path=ledger)) == 3


# --------------------------------------------------------------------------
# Ledger durability
# --------------------------------------------------------------------------

def test_a_corrupt_ledger_does_not_take_down_the_dashboard(ledger):
    ledger.write_text("{ this is not json")
    assert rq.load_ledger(ledger) == {}
    # And the next write must repair it rather than compounding the damage.
    rq.record_decision("clip_1", rq.APPROVED, path=ledger, mirror_to_clickhouse=False)
    assert json.loads(ledger.read_text())["clip_1"]["status"] == rq.APPROVED


def test_a_ledger_holding_a_json_list_is_treated_as_empty(ledger):
    ledger.write_text("[]")
    assert rq.load_ledger(ledger) == {}


def test_missing_ledger_reads_empty(tmp_path):
    assert rq.load_ledger(tmp_path / "nope.json") == {}


def test_no_temp_file_is_left_behind(ledger):
    rq.record_decision("clip_1", rq.APPROVED, path=ledger, mirror_to_clickhouse=False)
    assert not list(ledger.parent.glob("*.tmp"))


# --------------------------------------------------------------------------
# Archiving
# --------------------------------------------------------------------------

def test_rejection_archives_rather_than_deletes(clip_files):
    moved = rq.archive_rejected("clip_1", output_dir=clip_files)
    assert len(moved) == 4
    assert not (clip_files / "clip_1_9x16.mp4").exists()
    assert (clip_files / "rejected" / "clip_1_9x16.mp4").exists()
    # The metadata must travel with it, or the archive can't be explained.
    assert (clip_files / "rejected" / "clip_1_metadata.json").exists()


def test_publishing_archives_the_local_copy(clip_files):
    """
    Regression guard: the dashboard used to unlink the render right after
    a successful upload, so the local copy of a clip that just went live
    disappeared with no way to re-check what was published.
    """
    rq.archive_published("clip_1", output_dir=clip_files)
    assert (clip_files / "published" / "clip_1_9x16.mp4").exists()


def test_archiving_a_clip_with_no_files_is_a_noop(clip_files):
    assert rq.archive_rejected("clip_nonexistent", output_dir=clip_files) == []


def test_archiving_moves_only_the_named_clip(clip_files):
    (clip_files / "clip_2_9x16.mp4").write_text("x")
    rq.archive_rejected("clip_1", output_dir=clip_files)
    assert (clip_files / "clip_2_9x16.mp4").exists()


# --------------------------------------------------------------------------
# Email reply parsing
# --------------------------------------------------------------------------

@pytest.mark.parametrize("subject,decision,clip", [
    ("[NornPulse] APPROVE clip_1", "APPROVE", "clip_1"),
    ("Re: [NornPulse] REJECT clip_2", "REJECT", "clip_2"),
    ("re: [nornpulse] approve batch0_clip_001", "approve", "batch0_clip_001"),
    ("Fwd: [NornPulse]  REJECT   clip_3", "REJECT", "clip_3"),
])
def test_subject_parsing(subject, decision, clip):
    m = SUBJECT_RE.search(subject)
    assert m and m.group(1) == decision and m.group(2) == clip


@pytest.mark.parametrize("subject", [
    "[NornPulse Staged] Origin of Human Matter (Virality: 72.5/100)",  # the outgoing mail
    "[NornPulse] MAYBE clip_1",
    "APPROVE clip_1",
])
def test_subjects_that_must_not_parse_as_decisions(subject):
    assert SUBJECT_RE.search(subject) is None


def test_comment_stops_at_the_marker():
    marker = NornPublisher.REPLY_MARKER
    body = f"Great hook, ship it.\n\n{marker}\nDecision: APPROVE\nClip: clip_1\n"
    assert extract_comment(body, marker) == "Great hook, ship it."


def test_quoted_original_is_not_recorded_as_the_comment():
    """Without this, every comment would carry the whole original email back."""
    marker = NornPublisher.REPLY_MARKER
    body = (
        "Audio clips at the end.\n\n"
        "On Fri, Aug 22, 2026 at 8:15 PM NornPulse <x@y.com> wrote:\n"
        "> NornPulse has generated and staged a new vertical short\n"
        "> Clip ID: clip_1\n"
    )
    assert extract_comment(body, marker) == "Audio clips at the end."


def test_an_empty_reply_yields_an_empty_comment():
    marker = NornPublisher.REPLY_MARKER
    assert extract_comment(f"\n\n{marker}\nDecision: REJECT\n", marker) == ""


def test_a_reply_without_the_marker_still_yields_a_comment():
    """Some clients strip the prefilled body; the decision is in the subject anyway."""
    assert extract_comment("Nope, the cut is wrong.", NornPublisher.REPLY_MARKER) == \
        "Nope, the cut is wrong."


# --------------------------------------------------------------------------
# The mailto links
# --------------------------------------------------------------------------

def test_decision_links_encode_a_parseable_subject():
    pub = NornPublisher()
    pub.notify_email = "reviewer@example.com"
    for decision in ("approve", "reject"):
        url = pub._decision_mailto("clip_1", decision)
        assert url.startswith("mailto:reviewer@example.com?")
        # The subject must survive URL-encoding and still match the parser
        # that check_approvals.py runs against the reply.
        from urllib.parse import parse_qs, urlparse
        subject = parse_qs(urlparse(url).query)["subject"][0]
        m = SUBJECT_RE.search(subject)
        assert m.group(1).upper() == decision.upper()
        assert m.group(2) == "clip_1"


def test_the_email_carries_both_decision_buttons(tmp_path, monkeypatch):
    import smtplib
    from email import message_from_string

    sent = []

    class _SMTP:
        def __init__(self, *a): pass
        def starttls(self): pass
        def login(self, *a): pass
        def sendmail(self, f, t, m): sent.append(m)
        def quit(self): pass

    monkeypatch.setattr(smtplib, "SMTP", _SMTP)
    video = tmp_path / "clip_1_9x16.mp4"; video.write_bytes(b"x")

    pub = NornPublisher()
    pub.gmail_user, pub.gmail_password = "s@e.com", "pw"
    pub.notify_email = "r@e.com"
    assert pub.send_gmail_staged_approval("clip_1", "T", 70.0, str(video)) is True

    msg = message_from_string(sent[-1])
    html = next(p.get_payload(decode=True).decode()
                for p in msg.walk() if p.get_content_subtype() == "html")
    assert "APPROVE%20clip_1" in html and "REJECT%20clip_1" in html
