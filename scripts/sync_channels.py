# scripts/sync_channels.py
"""
Refresh channels.json's subscriber counts from real YouTube data.

channels.json's own subscriber counts are hand-typed and only ever change
when someone edits the file -- but that number selects the size band
(agent.global_benchmarks.size_band_for) every grounded decision in the app
is read within: hook ranking, calibration, the reach forecast. A stale
value silently changes what the pipeline recommends, with nothing on
screen to say it's stale.

    python scripts/sync_channels.py              # sync everything
    python scripts/sync_channels.py --dry-run    # show what would change

Runs unattended, same reasoning as sync_stats.py: it uses YOUTUBE_API_KEY
rather than OAuth, because this project's consent screen is in Testing and
Google expires those refresh tokens after 7 days -- a scheduled job on the
OAuth path would break every week. Subscriber counts are public, so a key
reads them fine.

Schedule it with cron, e.g. once a day:
    0 6 * * * cd /path/to/nornpulse && venv/bin/python scripts/sync_channels.py >> sync.log 2>&1
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report, change nothing")
    ap.add_argument("--config", default=None,
                     help="channels.json path (defaults to agent.channels.CONFIG_PATH)")
    args = ap.parse_args()

    from agent import channels as chans
    from agent.global_benchmarks import size_band_for
    from agent.norn_publisher import NornPublisher, PublishError

    config_path = Path(args.config) if args.config else chans.CONFIG_PATH
    if not config_path.exists():
        print(f"❌ {config_path} does not exist.")
        return 1

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    entries = raw.get("channels", {})
    if not entries:
        print(f"⚠️  {config_path} has no channels configured.")
        return 0

    publisher = NornPublisher()
    if not publisher.youtube_api_key:
        print("⚠️  YOUTUBE_API_KEY is not set — falling back to OAuth, which "
              "cannot run unattended for more than 7 days.")

    changed = unchanged = skipped = failed = 0
    for slug, entry in entries.items():
        youtube_channel_id = entry.get("youtube_channel_id", "")
        if not youtube_channel_id:
            print(f"  {slug}: no youtube_channel_id configured — skipped")
            skipped += 1
            continue

        old = int(entry.get("subscribers", 0))
        try:
            stats = publisher.get_channel_statistics(youtube_channel_id)
        except PublishError as e:
            print(f"  {slug}: {e}")
            failed += 1
            continue

        new = stats["subscriber_count"]
        old_band, new_band = size_band_for(old), size_band_for(new)
        band_note = f" — band changes {old_band} -> {new_band}!" if old_band != new_band else ""

        if new == old:
            print(f"  {slug}: {new:,} subscribers (unchanged)")
            unchanged += 1
            continue

        print(f"  {slug}: {old:,} -> {new:,} subscribers{band_note}")
        changed += 1
        if not args.dry_run:
            entry["subscribers"] = new

    if changed and not args.dry_run:
        config_path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote {config_path}.")

    print(f"\n{'(dry run) ' if args.dry_run else ''}"
          f"{changed} changed, {unchanged} unchanged, {skipped} skipped, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
