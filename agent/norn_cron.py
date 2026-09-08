# agent/norn_cron.py
"""
⚡ NornPulse: Scheduled staging (norn_cron.py)
Norn Labs (nornlabs.ai)

A timer that fills the review queue on its own, and stops there.

Nothing here uploads. It runs `trend_publish.py --generate --stage` for
each configured channel — the same script a human runs by hand, as a
subprocess rather than a second copy of its ~300 lines of critic/rights-
check/footage/shortsmith orchestration. A scheduler that stages is a
scheduler that fills a review queue, which is safe to be wrong; a
scheduler that publishes is not, and this project's rule is that a
pipeline stage gets automated only once its output is being approved
consistently in human review — which the trend loop's output is not yet.
check_approvals.py is still the only thing that uploads.

The previous version of this file predated the trend loop entirely (it
called SkuldRenderer directly with a hardcoded 15-second window and the
long-deprecated `google.generativeai` SDK) and was never imported by
anything. This is a full rewrite against the pipeline as it actually
exists today, not a patch.

Cost is the reason this isn't unconditional: --generate bills a Veo call
per attempt that finds a usable topic. `--max-per-day` (default 2, per
channel) is tracked in a small local state file so a channel that already
staged enough today is skipped rather than run again on the next cron
firing a few hours later.

    python -m agent.norn_cron                      # every configured channel
    python -m agent.norn_cron --channel nornpulse   # one channel
    python -m agent.norn_cron --max-per-day 1

Schedule it with cron, e.g. every 4 hours (giving several chances to hit
the cap across a day, since "nothing trending suited this channel" is a
real, free, non-error outcome that doesn't count against it):

    0 */4 * * * cd /path/to/nornpulse && venv/bin/python -m agent.norn_cron >> norn_cron.log 2>&1
"""

import argparse
import json
import logging
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nornpulse.cron")

# Local, gitignored -- this is scheduling state, not configuration. Kept
# next to .nornpulse_last_session.json's own precedent for the same reason:
# it describes what THIS machine's cron has already done today, not
# anything another checkout or the deployed service should inherit.
STATE_PATH = Path(".norn_cron_state.json")

# What trend_publish.py --stage actually prints on each real outcome --
# the signal this module keys off of, since the script's own exit code is
# 0 for both "staged" and "nothing trending suited this channel" (both are
# legitimate non-error outcomes for a human running it by hand, so that
# convention is right for trend_publish.py and is not something this
# wrapper should change under it).
_STAGED_MARKER = "sent. Reply APPROVE or REJECT"
_NO_TOPIC_MARKER = "Nothing trending suits this channel right now"


def _load_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning(f"{state_path} is unreadable; treating as empty.")
        return {}


def _today_count(channel_slug: str, state_path: Path = STATE_PATH) -> int:
    entry = _load_state(state_path).get(channel_slug, {})
    if entry.get("date") != date.today().isoformat():
        return 0
    return int(entry.get("count", 0))


def _record_staged(channel_slug: str, state_path: Path = STATE_PATH) -> None:
    state = _load_state(state_path)
    today = date.today().isoformat()
    entry = state.get(channel_slug, {})
    count = entry.get("count", 0) + 1 if entry.get("date") == today else 1
    state[channel_slug] = {"date": today, "count": count}
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def stage_one(channel_slug: str, max_per_day: int = 2,
              state_path: Path = STATE_PATH, timeout_sec: int = 900) -> str:
    """
    Runs trend_publish.py --generate --stage for one channel, unless
    today's cap is already spent.

    Returns "staged", "no_topic", "skipped_cap", or "failed" -- a caller
    counts anything but the first two as worth looking at, not necessarily
    "failed": no_topic is the honest, expected outcome on a day nothing
    trending suits the channel, and costs nothing.
    """
    already = _today_count(channel_slug, state_path)
    if already >= max_per_day:
        logger.info(f"{channel_slug}: already staged {already}/{max_per_day} "
                    f"today — skipping.")
        return "skipped_cap"

    logger.info(f"{channel_slug}: running trend_publish.py --generate --stage "
                f"({already}/{max_per_day} staged today)...")
    try:
        proc = subprocess.run(
            [sys.executable, "trend_publish.py", "--channel", channel_slug,
             "--generate", "--stage", "--verbose"],
            capture_output=True, text=True, timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        logger.error(f"{channel_slug}: trend_publish.py did not finish within "
                     f"{timeout_sec}s.")
        return "failed"

    output = proc.stdout + proc.stderr
    logger.info(output[-2000:])  # tail -- a generation call can log a lot

    if _STAGED_MARKER in output:
        _record_staged(channel_slug, state_path)
        logger.info(f"{channel_slug}: staged and emailed for approval.")
        return "staged"
    if _NO_TOPIC_MARKER in output:
        logger.info(f"{channel_slug}: nothing trending suited it this run.")
        return "no_topic"

    logger.error(f"{channel_slug}: exited {proc.returncode} without staging "
                 f"anything. Last output:\n{output[-500:]}")
    return "failed"


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--channel", action="append", dest="channels",
                     help="channel slug to stage for. Repeatable. Defaults to "
                          "every channel in channels.json.")
    ap.add_argument("--max-per-day", type=int, default=2,
                     help="staging attempts per channel per day (default 2). "
                          "Each one that finds a topic bills a Veo generation.")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    from agent import channels as chans
    slugs = args.channels or [c.slug for c in chans.list_channels()]

    results = {slug: stage_one(slug, max_per_day=args.max_per_day) for slug in slugs}
    logger.info(f"Done: {results}")
    return 1 if any(r == "failed" for r in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
