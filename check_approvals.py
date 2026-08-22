# check_approvals.py
"""
Read approve/reject replies from Gmail and apply them.

The staging email's buttons are mailto: links, so a decision arrives as
an ordinary reply whose subject carries the verdict:

    [NornPulse] APPROVE clip_1
    [NornPulse] REJECT  clip_2

Everything the reviewer typed above the marker line becomes the comment.
This needs no public callback URL, so it works identically before and
after the app is deployed.

    python check_approvals.py --channel UCxxxx [--privacy public] [--dry-run]

Approved clips are uploaded and logged; rejected clips are archived with
their comment. Messages are only marked read once acted on, so a crash
mid-run leaves the decision to be picked up next time rather than
swallowing it.
"""

import argparse
import email
import imaplib
import json
import logging
import os
import re
import sys
from email.header import decode_header, make_header
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("nornpulse.approvals")

IMAP_HOST = "imap.gmail.com"
# clip ids are [A-Za-z0-9_.-] by construction. \S+ would also swallow any
# punctuation a mail client appends after the id.
SUBJECT_RE = re.compile(r"\[NornPulse\]\s*(APPROVE|REJECT)\s+([\w.-]+)", re.IGNORECASE)
OUTPUT_DIR = Path("output_clips")


def _decode(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value or ""


def _body_text(msg) -> str:
    """First text/plain part, ignoring attachments."""
    if not msg.is_multipart():
        payload = msg.get_payload(decode=True) or b""
        return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    for part in msg.walk():
        if part.get_content_type() == "text/plain" and \
                not (part.get("Content-Disposition") or "").startswith("attachment"):
            payload = part.get_payload(decode=True) or b""
            return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return ""


def extract_comment(body: str, marker: str) -> str:
    """
    Everything above the marker is the reviewer's comment. Quoted history
    below it (and Gmail's "On ... wrote:" block) is dropped, otherwise
    every comment would carry the entire original email back with it.
    """
    head = body.split(marker, 1)[0] if marker in body else body
    lines = []
    for line in head.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if re.match(r"^On .+ wrote:$", stripped):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def fetch_decisions(marker: str):
    """Yield (uid, decision, clip_id, comment) for unread NornPulse replies."""
    user, password = os.getenv("GMAIL_USER"), os.getenv("GMAIL_APP_PASSWORD")
    if not user or not password:
        raise SystemExit("❌ GMAIL_USER / GMAIL_APP_PASSWORD are not set.")

    imap = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        imap.login(user, password)
        imap.select("INBOX")
        # Gmail's IMAP SUBJECT search is substring-based; the regex below
        # is what actually validates the format.
        status, data = imap.search(None, '(UNSEEN SUBJECT "[NornPulse]")')
        if status != "OK":
            return

        for uid in data[0].split():
            status, raw = imap.fetch(uid, "(BODY.PEEK[])")   # PEEK: don't mark read yet
            if status != "OK" or not raw or not raw[0]:
                continue
            msg = email.message_from_bytes(raw[0][1])
            match = SUBJECT_RE.search(_decode(msg.get("Subject", "")))
            if not match:
                continue
            decision, clip_id = match.group(1).lower(), match.group(2).strip()
            yield uid, decision, clip_id, extract_comment(_body_text(msg), marker), imap
    finally:
        try:
            imap.close(); imap.logout()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", help="expected YouTube channel id (required unless --dry-run)")
    ap.add_argument("--privacy", default="public", choices=["private", "unlisted", "public"])
    ap.add_argument("--dry-run", action="store_true", help="show decisions, change nothing")
    args = ap.parse_args()

    if not args.channel and not args.dry_run:
        raise SystemExit("❌ --channel is required unless --dry-run (uploading to the wrong channel is silent).")

    from agent import review_queue as rq
    from agent.norn_publisher import NornPublisher, PublishError
    from agent.urdr_analytics import UrdrAnalytics

    publisher = NornPublisher()
    urdr = None
    seen = 0

    for uid, decision, clip_id, comment, imap in fetch_decisions(publisher.REPLY_MARKER):
        seen += 1
        shown = comment.replace("\n", " ")[:70] or "(no comment)"
        print(f"\n📨 {decision.upper()} {clip_id} — {shown}")
        if args.dry_run:
            continue

        sidecar = OUTPUT_DIR / f"{clip_id}_metadata.json"
        clip = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}

        if decision == "reject":
            rq.record_decision(clip_id, rq.REJECTED, comment, source="email")
            moved = rq.archive_rejected(clip_id)
            print(f"   🗑️  archived {len(moved)} file(s) to output_clips/rejected/")

        else:
            # A duplicate reply, a forwarded thread, or a clip already
            # published from the dashboard must not produce a second
            # upload — that failure is silent and public.
            prior = rq.get_decision(clip_id)
            if prior and prior.get("status") == rq.APPROVED and prior.get("youtube_url"):
                print(f"   ⏭️  already published at {prior['youtube_url']} — skipping re-upload.")
                imap.store(uid, "+FLAGS", "\\Seen")
                continue

            video = clip.get("output_video_path")
            if not video or not Path(video).exists():
                print(f"   ❌ no rendered video for {clip_id}; leaving the reply unread.")
                continue
            try:
                from googleapiclient.discovery import build
                items = build("youtube", "v3", credentials=publisher._get_youtube_credentials()) \
                    .channels().list(part="snippet", mine=True).execute().get("items", [])
                got = items[0]["id"] if items else None
                if got != args.channel:
                    print(f"   ❌ token is bound to {got}, not {args.channel}. "
                          f"Run: python reauth_youtube.py {args.channel}")
                    return 1

                res = publisher.upload_to_youtube_shorts(
                    video_path=video,
                    title=clip.get("hook_title", clip_id),
                    description=clip.get("social_caption", ""),
                    privacy_status=args.privacy,
                    thumbnail_path=clip.get("thumbnail_path"),
                )
            except PublishError as e:
                print(f"   ❌ upload failed: {e}")
                continue

            print(f"   ✨ {res['url']} ({res['privacy_status']})")
            urdr = urdr or UrdrAnalytics()
            hook_type = clip.get("hook_type", "unknown")
            predicted_3s = float(clip.get("predicted_3s_retention_pct") or 0.0)
            if not predicted_3s:
                bench = urdr.query_hook_retention(hook_category=hook_type, limit=1)
                predicted_3s = float(bench.iloc[0]["avg_3s_retention_pct"]) if not bench.empty else 85.0
            urdr.log_published_outcome(
                clip_id=clip_id, youtube_video_id=res["video_id"], youtube_url=res["url"],
                hook_type=hook_type,
                predicted_virality_score=float(clip.get("virality_score", 0.0)),
                predicted_3s_retention_pct=predicted_3s,
            )
            rq.record_decision(clip_id, rq.APPROVED, comment, source="email",
                               extra={"youtube_url": res["url"], "youtube_video_id": res["video_id"]})
            rq.archive_published(clip_id)

        # Only now is the reply consumed — a crash above leaves it unread
        # so the decision is picked up on the next run instead of lost.
        imap.store(uid, "+FLAGS", "\\Seen")

    print(f"\n✅ Processed {seen} decision(s)." if seen else "📭 No new decisions.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
