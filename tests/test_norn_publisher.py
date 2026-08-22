"""
Unit tests for the HITL Gmail staging email.

Nothing here opens an SMTP connection: smtplib.SMTP is replaced with a
recorder, so the assembled MIME message can be inspected directly. The
things worth guarding are the ones that fail silently in a mail client
rather than raising — a missing inline cover, an unescaped ampersand
mangling the body, or a partial clip record rendering "None" at a
reviewer.
"""

import smtplib
from email import message_from_string
from email.header import decode_header, make_header

import pytest

from agent.norn_publisher import NornPublisher


class _RecordingSMTP:
    sent: list[str] = []

    def __init__(self, host, port):
        pass

    def starttls(self): pass
    def login(self, u, p): pass
    def sendmail(self, frm, to, msg): _RecordingSMTP.sent.append(msg)
    def quit(self): pass


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "clip_1_9x16.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42fake-video-bytes")
    return p


@pytest.fixture
def thumb(tmp_path):
    # A one-pixel JPEG is enough for MIMEImage to type it correctly.
    p = tmp_path / "clip_1_thumb.jpg"
    p.write_bytes(bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb004300"
        + "ff" * 64 + "ffd9"))
    return p


@pytest.fixture
def publisher(monkeypatch):
    _RecordingSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _RecordingSMTP)
    pub = NornPublisher()
    pub.gmail_user = "sender@example.com"
    pub.gmail_password = "app-password"
    pub.notify_email = "reviewer@example.com"
    return pub


CLIP = {
    "clip_id": "clip_1", "start_time": "00:22", "end_time": "00:34",
    "social_caption": "Rock & roll <stars> are us",
    "hook_type": "curiosity_gap", "hook_rank": 3,
    "crop_mode": "blurred_background", "motion_effect": "ken_burns_zoom",
    "color_grade": "cool_desaturated", "caption_language": "Spanish",
    "music_genre": "synthwave", "music_mood": "mysterious",
    "has_subtitles": True, "has_bragi_score": True, "has_narration": False,
    "is_top_tier_hook": True,
}


def _send(publisher, video, **kw):
    ok = publisher.send_gmail_staged_approval(
        clip_id="clip_1", title="Origin of Human Matter",
        virality=72.5, video_path=str(video), **kw)
    assert ok is True
    return message_from_string(_RecordingSMTP.sent[-1])


def _part_bodies(msg, subtype):
    return [p.get_payload(decode=True).decode("utf-8")
            for p in msg.walk() if p.get_content_subtype() == subtype]


# --------------------------------------------------------------------------
# Enriched review fields
# --------------------------------------------------------------------------

def test_review_fields_reach_both_bodies(publisher, video):
    msg = _send(publisher, video, clip=CLIP)
    for body in _part_bodies(msg, "plain") + _part_bodies(msg, "html"):
        assert "curiosity_gap" in body
        assert "ken_burns_zoom" in body
        assert "cool_desaturated" in body
        assert "Spanish" in body
        assert "00:22" in body and "00:34" in body


def test_boolean_flags_render_as_a_single_includes_row():
    rows = dict(NornPublisher._review_rows(CLIP))
    assert "kinetic subtitles" in rows["Includes"]
    assert "original Bragi score" in rows["Includes"]
    # has_narration is False, so it must not be advertised.
    assert "Mímir" not in rows["Includes"]


def test_missing_fields_are_omitted_not_rendered_as_none():
    """
    A partial record (an older clip, or one where Heimdall failed) must
    still produce a clean table rather than showing a reviewer "None".
    """
    rows = NornPublisher._review_rows({"hook_type": "shock_stat"})
    assert rows == [("Hook type", "shock_stat")]
    assert all("None" not in v for _, v in rows)


def test_empty_clip_still_sends(publisher, video):
    """The enrichment is optional — the old 4-argument call must still work."""
    msg = _send(publisher, video)
    assert "Origin of Human Matter" in _part_bodies(msg, "plain")[0]


# --------------------------------------------------------------------------
# HTML escaping
# --------------------------------------------------------------------------

def test_model_authored_text_is_escaped_in_the_html_body(publisher, video):
    """
    hook_title and social_caption are Gemini output. An unescaped & or <
    doesn't raise — it silently mangles the rendered email, which is
    exactly the kind of failure nobody notices until a reviewer sees a
    broken message.
    """
    html = _part_bodies(publisher and _send(publisher, video, clip=CLIP), "html")[0]
    assert "Rock &amp; roll &lt;stars&gt; are us" in html
    assert "<stars>" not in html


def test_plain_text_body_is_not_escaped(publisher, video):
    """Escaping belongs to the HTML alternative only."""
    plain = _part_bodies(_send(publisher, video, clip=CLIP), "plain")[0]
    assert "Rock & roll <stars> are us" in plain


# --------------------------------------------------------------------------
# MIME structure
# --------------------------------------------------------------------------

def test_video_is_attached_and_cover_is_inline(publisher, video, thumb):
    msg = _send(publisher, video, clip=CLIP, thumbnail_path=str(thumb))

    attachments = [p for p in msg.walk()
                   if p.get("Content-Disposition", "").startswith("attachment")]
    assert [p.get_filename() for p in attachments] == ["clip_1_9x16.mp4"]

    images = [p for p in msg.walk() if p.get_content_maintype() == "image"]
    assert len(images) == 1
    assert images[0].get("Content-ID") == "<heimdall_cover>"
    # The cid must match what the HTML references, or the cover shows broken.
    assert 'src="cid:heimdall_cover"' in _part_bodies(msg, "html")[0]


def test_thumbnail_falls_back_to_the_clip_record(publisher, video, thumb):
    msg = _send(publisher, video, clip={**CLIP, "thumbnail_path": str(thumb)})
    assert any(p.get_content_maintype() == "image" for p in msg.walk())


def test_a_missing_thumbnail_degrades_instead_of_failing(publisher, video):
    """Heimdall 503s happen; losing the cover must not lose the email."""
    msg = _send(publisher, video, clip=CLIP, thumbnail_path="/nope/missing.jpg")
    assert not any(p.get_content_maintype() == "image" for p in msg.walk())
    assert "cid:heimdall_cover" not in _part_bodies(msg, "html")[0]


def test_subject_carries_title_and_score(publisher, video):
    msg = _send(publisher, video, clip=CLIP)
    # The leading emoji forces RFC 2047 encoding, so decode before asserting.
    subject = str(make_header(decode_header(msg["Subject"])))
    assert "Origin of Human Matter" in subject
    assert "72.5" in subject


# --------------------------------------------------------------------------
# Failure handling
# --------------------------------------------------------------------------

def test_missing_video_returns_false(publisher):
    assert publisher.send_gmail_staged_approval(
        "clip_1", "T", 50.0, "/nope/missing.mp4") is False


def test_missing_credentials_returns_false_without_sending(video):
    pub = NornPublisher()
    pub.gmail_user = None
    assert pub.send_gmail_staged_approval("clip_1", "T", 50.0, str(video)) is False


def test_smtp_failure_returns_false_rather_than_raising(publisher, video, monkeypatch):
    """A staged clip already exists on disk; a mail outage must not lose the run."""
    def _boom(*a, **kw):
        raise smtplib.SMTPAuthenticationError(535, b"bad creds")
    monkeypatch.setattr(smtplib, "SMTP", _boom)
    assert publisher.send_gmail_staged_approval(
        "clip_1", "T", 50.0, str(video)) is False
