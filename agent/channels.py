# agent/channels.py
"""
⚡ NornPulse: Channel registry and publishing profiles (channels.py)
Norn Labs (nornlabs.ai)

Which channel a clip is being made for, and what that implies.

Until now the pipeline assumed exactly one destination. The OAuth token
lived at a single hardcoded path, the YouTube category was hardcoded to 28
(Science & Technology), and "what size is this channel?" was a number typed
into the sidebar. That is fine for one channel and wrong for two.

It is wrong in a way that matters to the product's own thesis. The central
claim is that the right decision depends on the size band of the channel
publishing it — captions are -4% reach at 0-100 subscribers and +34% at
100K-1M. A pipeline that cannot tell which channel it is publishing to
cannot act on its own finding, and cannot compare the two.

So a channel is a first-class object with:

  identity   — the YouTube channel id, and its own OAuth token file, so
               publishing to the wrong channel is not possible by accident
               (a token silently bound to the wrong channel has already
               cost one debugging session)
  size       — subscriber count, which selects the size band that every
               grounded decision is read within
  profile    — the content-shaped defaults: category, caption face, music
               mood, and which hook types suit the material

The profile is deliberately thin. It sets defaults and category metadata;
it does not override anything Urðr measures. A comedy channel gets a
comedy category and a punchier caption face, but its hook ranking still
comes from the data, not from the profile.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(os.getenv("NORNPULSE_CHANNELS_CONFIG", "channels.json"))
CREDENTIALS_DIR = Path(".credentials")

# YouTube category ids. Only the ones we actually publish into.
CATEGORY_SCIENCE_TECH = "28"
CATEGORY_COMEDY = "23"
CATEGORY_ENTERTAINMENT = "24"
CATEGORY_EDUCATION = "27"

VALID_CATEGORIES = {
    CATEGORY_SCIENCE_TECH: "Science & Technology",
    CATEGORY_COMEDY: "Comedy",
    CATEGORY_ENTERTAINMENT: "Entertainment",
    CATEGORY_EDUCATION: "Education",
}


@dataclass
class ChannelProfile:
    """Content-shaped defaults for a channel. Never overrides measured data."""

    category_id: str = CATEGORY_SCIENCE_TECH
    caption_font: Optional[str] = None
    music_mood: Optional[str] = None
    # Hook types this channel's material tends to suit. Used only to break
    # ties when the measured ranking is close; it never promotes a hook
    # above one the data ranks higher.
    preferred_hooks: List[str] = field(default_factory=list)
    # Appended to every upload's tag candidates, so they are still subject
    # to the same relevance and provenance rules as anything else.
    topic_hints: List[str] = field(default_factory=list)
    # Motion effects this channel will not use, whatever the benchmarks say.
    # The benchmarks are seeded priors ranked on a generic taxonomy, and
    # "shake" tops them, so without this it is chosen for every clip -- which
    # is how a NASA explainer came back wobbling and was rejected as "too
    # bouncy". A genuine editorial constraint, not a measurement.
    avoid_motion: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.category_id not in VALID_CATEGORIES:
            raise ValueError(
                f"Unknown YouTube category id {self.category_id!r}. "
                f"Known: {sorted(VALID_CATEGORIES)}"
            )


@dataclass
class Channel:
    """One publishing destination."""

    slug: str
    youtube_channel_id: str
    title: str
    subscribers: int = 0
    profile: ChannelProfile = field(default_factory=ChannelProfile)

    @property
    def token_path(self) -> Path:
        """
        Where this channel's OAuth token lives.

        Per-channel rather than shared: one token can only ever be bound to
        one channel, and a shared path means re-authorising for channel B
        silently overwrites the credentials for channel A. That failure is
        invisible until an upload lands on the wrong channel.
        """
        return CREDENTIALS_DIR / f"youtube_token_{self.slug}.json"

    @property
    def legacy_token_path(self) -> Path:
        """The single-channel token path used before channels existed."""
        return CREDENTIALS_DIR / "youtube_token.json"

    def resolve_token_path(self) -> Path:
        """
        The token to actually use.

        Falls back to the pre-channels path when this channel has no token
        of its own but the legacy one exists, so an existing setup keeps
        working without a forced re-authorisation. The fallback is only
        offered to the default channel: handing channel B the token that
        was authorised for channel A is exactly the accident the
        per-channel paths exist to prevent.
        """
        if self.token_path.exists():
            return self.token_path
        if self.slug == DEFAULT_SLUG and self.legacy_token_path.exists():
            logger.info(
                f"Channel '{self.slug}' has no token of its own; using the "
                f"pre-channels token at {self.legacy_token_path}."
            )
            return self.legacy_token_path
        return self.token_path

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slug": self.slug,
            "youtube_channel_id": self.youtube_channel_id,
            "title": self.title,
            "subscribers": self.subscribers,
            "profile": {
                "category_id": self.profile.category_id,
                "caption_font": self.profile.caption_font,
                "music_mood": self.profile.music_mood,
                "preferred_hooks": list(self.profile.preferred_hooks),
                "topic_hints": list(self.profile.topic_hints),
                "avoid_motion": list(self.profile.avoid_motion),
            },
        }


DEFAULT_SLUG = "nornpulse"

# Shipped defaults. channels.json overrides these when present, so the
# committed file stays free of anything that needs to change per install.
_BUILTIN: Dict[str, Channel] = {
    "nornpulse": Channel(
        slug="nornpulse",
        youtube_channel_id="UCbrN8oPKkAqhb7_JbWixOig",
        title="NornPulse",
        subscribers=0,
        profile=ChannelProfile(
            category_id=CATEGORY_SCIENCE_TECH,
            caption_font="Condensed — Roboto Condensed",
            music_mood="dramatic",
            preferred_hooks=["curiosity_gap", "shock_stat", "contrarian_claim"],
            topic_hints=["science", "space", "technology"],
        ),
    ),
    "sloptokdaily": Channel(
        slug="sloptokdaily",
        youtube_channel_id="",  # filled in from channels.json
        title="sloptokdaily",
        subscribers=0,
        profile=ChannelProfile(
            category_id=CATEGORY_COMEDY,
            caption_font="Impact — Roboto Black",
            music_mood="playful",
            preferred_hooks=["visual_disruption", "shock_stat", "direct_question"],
            topic_hints=["funny", "comedy", "ai"],
        ),
    ),
}


def _parse(raw: Dict[str, Any], slug: str) -> Channel:
    profile_raw = raw.get("profile") or {}
    base = _BUILTIN.get(slug)
    base_profile = base.profile if base else ChannelProfile()
    profile = ChannelProfile(
        category_id=str(profile_raw.get("category_id", base_profile.category_id)),
        caption_font=profile_raw.get("caption_font", base_profile.caption_font),
        music_mood=profile_raw.get("music_mood", base_profile.music_mood),
        preferred_hooks=list(profile_raw.get("preferred_hooks", base_profile.preferred_hooks)),
        topic_hints=list(profile_raw.get("topic_hints", base_profile.topic_hints)),
        avoid_motion=list(profile_raw.get("avoid_motion", base_profile.avoid_motion)),
    )
    return Channel(
        slug=slug,
        youtube_channel_id=str(raw.get("youtube_channel_id", base.youtube_channel_id if base else "")),
        title=str(raw.get("title", base.title if base else slug)),
        subscribers=int(raw.get("subscribers", base.subscribers if base else 0)),
        profile=profile,
    )


def load_channels(path: Optional[Path] = None) -> Dict[str, Channel]:
    """
    The channel registry: built-in defaults, overridden by channels.json.

    A malformed or missing config is not fatal — it falls back to the
    built-ins and logs, because losing the ability to publish anywhere is a
    worse outcome than running with stale subscriber counts.
    """
    channels = {slug: ch for slug, ch in _BUILTIN.items()}
    config_path = path or CONFIG_PATH
    if not config_path.exists():
        return channels
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Could not read {config_path}: {e}; using built-in channel defaults.")
        return channels
    for slug, entry in (raw.get("channels") or {}).items():
        try:
            channels[slug] = _parse(entry, slug)
        except Exception as e:
            logger.warning(f"Ignoring malformed channel '{slug}' in {config_path}: {e}")
    return channels


def list_channels(path: Optional[Path] = None) -> List[Channel]:
    return list(load_channels(path).values())


def get_channel(slug: Optional[str] = None, path: Optional[Path] = None) -> Channel:
    """
    Look up a channel by slug, defaulting to the primary one.

    Raises on an unknown slug rather than silently falling back: publishing
    to the wrong channel is the expensive mistake here, and a typo in a
    --channel flag should stop the run, not redirect it.
    """
    channels = load_channels(path)
    if slug is None:
        slug = os.getenv("NORNPULSE_CHANNEL", DEFAULT_SLUG)
    if slug not in channels:
        raise KeyError(
            f"Unknown channel '{slug}'. Known channels: {sorted(channels)}. "
            f"Add it to {path or CONFIG_PATH} to publish there."
        )
    return channels[slug]
