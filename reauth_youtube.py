# reauth_youtube.py
"""
Re-issue the cached YouTube OAuth token against a specific channel.

The token at .credentials/youtube_token.json is bound to whichever
account granted consent, and `_get_youtube_credentials` prefers it over
prompting. Switching channels without clearing it means uploads land on
the old channel with no warning — the failure is silent, and by the time
you notice, the video is public on the wrong account.

This script clears the token, runs consent, then *verifies* the channel
it actually got. If it doesn't match the one you asked for, the previous
token is restored and nothing is left in a half-changed state.

    python reauth_youtube.py --channel sloptokdaily
    python reauth_youtube.py UCxxxxxxxxxxxxxxxxxxxxxx   # by raw id

Each channel has its own token file, so authorising one never disturbs
another's credentials.

Needs a local browser and a free port 8080 — it will not work over SSH
or in the container.
"""

import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def reauth(target_channel_id: str, slug: str | None = None) -> int:
    from googleapiclient.discovery import build
    from agent import channels
    from agent.norn_publisher import NornPublisher

    channel = channels.get_channel(slug)
    # Write to this channel's own token path, never the shared legacy one:
    # re-authorising channel B must not overwrite channel A's credentials.
    token_path = channel.token_path
    print(f"📺 Channel: {channel.slug} ({channel.title}) → {token_path}")

    backup = None
    if token_path.exists():
        backup = token_path.with_suffix(f".json.bak-{int(time.time())}")
        token_path.rename(backup)
        print(f"↩️  Existing token moved aside to {backup.name}")

    pub = NornPublisher(channel)
    try:
        print("🔐 Opening the Google consent flow in your browser...")
        print("   If the new channel is a Brand Account, pick it from the "
              "account chooser — the default is your personal channel.")
        creds = pub._get_youtube_credentials()

        channels = build("youtube", "v3", credentials=creds).channels().list(
            part="snippet", mine=True).execute().get("items", [])
        if not channels:
            raise RuntimeError("Consent succeeded but no channel came back for this account.")

        got_id = channels[0]["id"]
        got_title = channels[0]["snippet"].get("title", "?")

        if got_id != target_channel_id:
            raise RuntimeError(
                f"Authorized the wrong channel: got {got_id} ('{got_title}'), "
                f"expected {target_channel_id}. Re-run and choose the other "
                f"channel in the account chooser."
            )

        print(f"✅ Token now bound to {got_id} ('{got_title}').")
        if backup:
            print(f"   The previous token is still at {backup.name}; delete it once you're happy.")
        return 0

    # BaseException, not Exception: the consent flow blocks on a local
    # callback that may never arrive (a denied or abandoned sign-in), and
    # Ctrl+C there must still put the old token back. Catching only
    # Exception leaves the token moved aside and the next publish silently
    # falls into an interactive re-auth.
    except BaseException as e:
        print(f"❌ {type(e).__name__}: {e}" if not isinstance(e, KeyboardInterrupt)
              else "❌ Interrupted before consent completed.")
        if backup:
            token_path.unlink(missing_ok=True)
            backup.rename(token_path)
            print("↩️  Restored the previous token — nothing was changed.")
        return 1


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("channel_id", nargs="?",
                    help="YouTube channel id to verify against. Defaults to "
                         "the configured id for --channel.")
    ap.add_argument("--channel", default=None,
                    help="channel slug from channels.json (default: primary)")
    args = ap.parse_args()

    from agent import channels as _chans
    try:
        _channel = _chans.get_channel(args.channel)
    except KeyError as e:
        print(f"❌ {e}")
        sys.exit(2)

    target = args.channel_id or _channel.youtube_channel_id
    if not target:
        print(f"❌ No youtube_channel_id configured for '{_channel.slug}' and "
              f"none given on the command line.")
        sys.exit(2)
    sys.exit(reauth(target, slug=args.channel))
