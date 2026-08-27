#!/usr/bin/env python3
"""
Set Heimdall's cover on clips that were published before the channel was
phone-verified.

    python scripts/apply_thumbnails.py --channel nornpulse [--dry-run]

Custom thumbnails need a phone-verified channel. Every clip published
before that verification uploaded fine and then failed one API call at the
end, logging a warning and carrying on — which was the right call at the
time (a cover is not worth losing a publish over) but left a backlog of
live videos wearing an auto-generated frame instead of the cover the
pipeline made for them.

This walks that backlog. It reads which video each clip became from
ClickHouse, finds the cover next to the render, and sets it.

Only clips whose cover still exists locally can be fixed: the image is not
stored anywhere else, and output_clips/ is excluded from the deployed
image, so this is a workstation-only repair.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

OUTPUT_DIR = Path("output_clips")
# Where a cover can be, in the order a clip moves through.
THUMB_DIRS = (OUTPUT_DIR / "published", OUTPUT_DIR, OUTPUT_DIR / "rejected")


def thumb_for(clip_id: str) -> Path | None:
    for d in THUMB_DIRS:
        for suffix in ("_thumb.jpg", "_thumb.png"):
            p = d / f"{clip_id}{suffix}"
            if p.exists():
                return p
    return None


def published_on(channel_slug: str) -> list[tuple[str, str]]:
    """
    (clip_id, youtube_video_id) for everything this channel published.

    Two sources, unioned. clip_publications is the analytics mirror and
    knows the channel, but it only goes back as far as the table does --
    the earliest clips were published before it existed and appear only in
    the local review ledger. The ledger in turn does not record a channel,
    so its extras are attempted anyway: a video this channel's token does
    not own fails the API call and is named in the output, which is a
    better outcome than silently skipping a cover that could have been set.
    """
    import agent.clickhouse_mcp_client as ch

    from agent import review_queue as rq

    found: dict[str, str] = {}
    try:
        rows = ch.run_query(
            "SELECT DISTINCT clip_id, youtube_video_id FROM clip_publications "
            f"WHERE channel_slug = {ch.sql_literal(channel_slug)} "
            "AND youtube_video_id != '' ORDER BY clip_id"
        )["rows"]
        found.update({r[0]: r[1] for r in rows})
    except Exception as e:
        print(f"⚠️  clip_publications unreadable ({str(e)[:70]}); using the ledger only.")

    for row in rq.published_urls():
        if row.get("video_id"):
            found.setdefault(row["clip_id"], row["video_id"])
    return sorted(found.items())


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--channel", required=True,
                    help="channel slug; must be phone-verified for custom thumbnails")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from googleapiclient.http import MediaFileUpload

    from agent import channels as chans
    from agent.norn_publisher import NornPublisher

    channel = chans.get_channel(args.channel)
    pairs = published_on(channel.slug)
    if not pairs:
        print(f"No published videos recorded for {channel.slug}.")
        return 0

    todo = [(c, v, thumb_for(c)) for c, v in pairs]
    missing = [c for c, _, t in todo if not t]
    todo = [(c, v, t) for c, v, t in todo if t]

    print(f"📺 {channel.slug}: {len(pairs)} published, {len(todo)} with a local cover")
    if missing:
        print(f"   no cover on disk for: {', '.join(missing)}")
    if args.dry_run:
        for clip_id, video_id, t in todo:
            print(f"   would set {t.name} on {video_id} ({clip_id})")
        return 0

    publisher = NornPublisher(channel)
    from googleapiclient.discovery import build
    youtube = build("youtube", "v3", credentials=publisher._get_youtube_credentials())

    ok = failed = 0
    for clip_id, video_id, thumb in todo:
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumb), mimetype="image/jpeg"),
            ).execute()
            ok += 1
            print(f"   ✅ {video_id} <- {thumb.name}")
        except Exception as e:
            failed += 1
            # The 403 here is the whole reason this script exists, so it is
            # named rather than buried: a channel that is still unverified
            # fails every row identically and the message should say so
            # once, not look like a hundred unrelated errors.
            reason = str(e)
            hint = ("  (channel not phone-verified — youtube.com/verify)"
                    if "forbidden" in reason.lower() or "403" in reason else "")
            print(f"   ❌ {video_id}: {reason[:100]}{hint}")

    print(f"\n{ok} set, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
