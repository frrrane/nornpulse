# agent/footage.py
"""
⚡ NornPulse: Copyright-clean footage sources (footage.py)
Norn Labs (nornlabs.ai)

Where a video comes from when nobody filmed it.

The trend loop needs footage for a topic that is trending right now. The
obvious source — the trending videos themselves — is the one that must never
be used. Re-cutting someone else's video is a copyright claim against the
channel, not a clever shortcut, and no amount of transformation in the
pipeline changes who owns the frames.

So there are exactly two clean sources here, behind one interface:

  VeoFootage        Generated from a text prompt. Nothing pre-existing is
                    reproduced, and it can be made 9:16 and short-form
                    natively, which is what the destination wants anyway.
                    It costs real money per second of output.

  WikimediaFootage  Public domain and freely-licensed material from
                    Wikimedia Commons. Free and fast, but you get what
                    exists rather than what you asked for, and the licence
                    of each file has to be checked rather than assumed —
                    "on Commons" does not mean "public domain".

Both return a local file path and a provenance record describing where the
frames came from, so a published clip can always answer that question.

Generated footage is 9:16 at source, so it does not pass through Skuld's
16:9-to-9:16 crop. The generated clip *is* the short.
"""

from __future__ import annotations

import logging
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Veo variants, cheapest first. The loop defaults to the cheapest that is
# good enough, because this is the only part of the pipeline that bills per
# second of output and an unattended loop can spend real money quickly.
VEO_LITE = "veo-3.1-lite-generate-preview"
VEO_FAST = "veo-3.1-fast-generate-preview"
VEO_FULL = "veo-3.1-generate-preview"
DEFAULT_VEO_MODEL = VEO_FAST

# Veo bills per second of generated video and the rate differs per variant
# and changes over time, so no figure is hardcoded here — quoting a stale
# price is worse than quoting none. Callers state the model and duration and
# let the operator check current pricing.
VEO_PRICING_URL = "https://ai.google.dev/gemini-api/docs/pricing"

# Long enough to be a real Short, short enough not to be expensive.
DEFAULT_DURATION_SEC = 8

POLL_INTERVAL_SEC = 10
POLL_TIMEOUT_SEC = 900

# gRPC codes that mean "the backend broke", not "your request was wrong".
# 13 INTERNAL, 14 UNAVAILABLE, 4 DEADLINE_EXCEEDED, 8 RESOURCE_EXHAUSTED.
TRANSIENT_ERROR_CODES = {4, 8, 13, 14}
_TRANSIENT_RE = re.compile(
    r"internal (server )?(issue|error)|unavailable|try again|temporarily|"
    r"deadline exceeded|overloaded", re.I)


def _looks_transient(message: str) -> bool:
    return bool(message and _TRANSIENT_RE.search(message))


@dataclass
class Footage:
    """A local video file, and an honest account of where it came from."""

    path: Path
    source: str                      # "generated" | "public_domain"
    provider: str                    # "veo-3.1-fast..." | "wikimedia"
    description: str                 # prompt, or the source file's title
    duration_sec: float = 0.0
    attribution: Optional[str] = None   # required for some Commons licences
    licence: Optional[str] = None
    url: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def needs_attribution(self) -> bool:
        """CC-BY and CC-BY-SA require credit; public domain does not."""
        return bool(self.licence and "by" in self.licence.lower())


class FootageError(RuntimeError):
    """Raised when footage could not be obtained. Never partially written."""


# ---------------------------------------------------------------------------
# Generated
# ---------------------------------------------------------------------------

def generate_with_veo(
    prompt: str,
    out_path: str | Path,
    model: str = DEFAULT_VEO_MODEL,
    duration_sec: int = DEFAULT_DURATION_SEC,
    aspect_ratio: str = "9:16",
    negative_prompt: Optional[str] = None,
    generate_audio: Optional[bool] = None,
    api_key: Optional[str] = None,
    poll_timeout_sec: int = POLL_TIMEOUT_SEC,
) -> Footage:
    """
    Generate a clip from a text prompt.

    Vertical at source: the destination is a Short, and asking for 9:16 here
    avoids a crop pass that would throw away half of every frame.

    Long-running by design — Veo returns an operation, not a video, and a
    minute or more of waiting is normal. The timeout exists so an unattended
    loop cannot block forever on a job that will never finish.
    """
    from google.genai import types

    from agent import genai_client as gc

    # On Vertex the credentials come from the environment, not a key.
    key = api_key or os.getenv("GEMINI_API_KEY")
    if not gc.use_vertex() and not key:
        raise FootageError("GEMINI_API_KEY is not set; cannot generate footage.")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Veo's name differs between the two surfaces, and unlike the text
    # models it is not served from `global`, so the factory returns the
    # name to use alongside a client already bound to the right region.
    client, model = gc.client_for(model, api_key=key)
    logger.info(f"Veo ({model}) generating {duration_sec}s {aspect_ratio}: {prompt[:80]}")

    config = types.GenerateVideosConfig(
        aspect_ratio=aspect_ratio,
        duration_seconds=duration_sec,
        number_of_videos=1,
    )
    if negative_prompt:
        config.negative_prompt = negative_prompt
    # generate_audio exists in the SDK but is only accepted on Vertex AI.
    # On the Gemini Developer API — the API-key path this project uses — the
    # request is rejected outright with "only supported in Gemini Enterprise
    # Agent Platform mode". Veo 3.x produces audio by default there anyway,
    # so the parameter is simply not sent unless a caller insists.
    if generate_audio is not None:
        logger.warning(
            "generate_audio is not accepted by the Gemini Developer API and "
            "will cause the request to be rejected; sending it because it was "
            "explicitly set.")
        config.generate_audio = generate_audio

    try:
        operation = client.models.generate_videos(
            model=model, prompt=prompt, config=config)
    except Exception as e:
        raise FootageError(f"Veo rejected the request: {e}") from e

    waited = 0
    while not operation.done:
        if waited >= poll_timeout_sec:
            raise FootageError(
                f"Veo did not finish within {poll_timeout_sec}s. The job may still "
                f"complete; nothing was written.")
        time.sleep(POLL_INTERVAL_SEC)
        waited += POLL_INTERVAL_SEC
        try:
            operation = client.operations.get(operation)
        except Exception as e:
            raise FootageError(f"Lost track of the Veo job: {e}") from e

    response = getattr(operation, "response", None) or getattr(operation, "result", None)
    videos = getattr(response, "generated_videos", None) if response else None
    if not videos:
        # Two very different failures arrive here and must not be reported
        # as one. A refusal is a completed operation carrying no video and
        # no error, and means the prompt needs changing. A backend fault
        # carries an error, and means try again — telling someone to rewrite
        # a prompt that was never the problem wastes their time and, if they
        # take the advice, their next generation too.
        err = getattr(operation, "error", None)
        code = None
        message = ""
        if err is not None:
            code = getattr(err, "code", None) or (
                err.get("code") if isinstance(err, dict) else None)
            message = str(getattr(err, "message", None) or (
                err.get("message") if isinstance(err, dict) else err))
        if code in TRANSIENT_ERROR_CODES or _looks_transient(message):
            raise FootageError(
                f"Veo hit a backend fault, not a problem with the prompt — "
                f"retry in a few minutes. ({message or code})")
        if err is not None:
            raise FootageError(f"Veo failed: {message or err}")
        raise FootageError(
            "Veo returned no video and reported no error, which usually means "
            "the prompt was refused. Try rewording it.")

    video = videos[0].video
    # How the finished bytes arrive differs by surface, and getting it
    # wrong costs a whole generation: the video is made and billed, and
    # then thrown away at the last step. Vertex returns the bytes on the
    # object itself; AI Studio returns a handle that has to be downloaded
    # through the Files API, which Vertex does not have at all.
    try:
        data = getattr(video, "video_bytes", None)
        if data:
            out_path.write_bytes(data)
        else:
            client.files.download(file=video)
            video.save(str(out_path))
    except Exception as e:
        raise FootageError(
            f"The video was generated but could not be saved: {e}") from e

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise FootageError(f"Veo reported success but {out_path} is empty.")

    return Footage(
        path=out_path,
        source="generated",
        provider=model,
        description=prompt,
        duration_sec=float(duration_sec),
        licence="generated",
        extra={"aspect_ratio": aspect_ratio},
    )


# ---------------------------------------------------------------------------
# Public domain
# ---------------------------------------------------------------------------

_COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# Licences safe to publish without attribution, or with simple credit.
# Anything not on this list is skipped rather than guessed at: an
# unrecognised licence string is not permission.
_ACCEPTABLE_LICENCES = {
    "cc0", "pd", "public domain", "cc-pd-mark", "cc-zero",
    "cc-by", "cc-by-sa", "cc by", "cc by-sa",
}


# Refused outright, before the allowlist is consulted. NonCommercial forbids
# the commercial use a monetisable channel implies, and NoDerivatives forbids
# the captioning, scoring and cutting this pipeline does. They have to be
# checked first: a plain substring test passes "CC BY-NC 4.0" because it
# contains "cc by", which is exactly the kind of quiet mistake that only
# surfaces later as a copyright strike.
_FORBIDDEN_RE = re.compile(
    r"(?:^|[\s\-_])(?:nc|nd|noncommercial|non-commercial|noderiv\w*)"
    r"(?=$|[\s\-_0-9])", re.I)


def _licence_ok(raw: Optional[str]) -> bool:
    if not raw:
        return False
    low = raw.strip().lower()
    if _FORBIDDEN_RE.search(low):
        return False
    return any(ok in low for ok in _ACCEPTABLE_LICENCES)


def search_wikimedia(query: str, limit: int = 10,
                     timeout: int = 30) -> List[Dict[str, Any]]:
    """
    Freely-licensed video files on Commons matching a query.

    Returns only files whose licence is recognisably free. Commons hosts
    material under many licences, some of which are not usable here, and
    "it was on Commons" is not a licence.
    """
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"filetype:video {query}", "gsrlimit": str(limit),
        "gsrnamespace": "6",
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
    }
    url = f"{_COMMONS_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "NornPulse/1.0 (https://nornlabs.ai) footage-search",
    })
    try:
        import json
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        logger.warning(f"Commons search failed: {e}")
        return []

    out: List[Dict[str, Any]] = []
    for page in (data.get("query", {}).get("pages") or {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        licence = (meta.get("LicenseShortName") or {}).get("value")
        if not _licence_ok(licence):
            continue
        out.append({
            "title": page.get("title", ""),
            "url": info.get("url"),
            "mime": info.get("mime"),
            "size": info.get("size"),
            "licence": licence,
            "artist": (meta.get("Artist") or {}).get("value"),
            "descriptionurl": info.get("descriptionurl"),
        })
    return out


def fetch_wikimedia(query: str, out_path: str | Path,
                    timeout: int = 120) -> Footage:
    """Download the first usable Commons match for a query."""
    results = search_wikimedia(query)
    if not results:
        raise FootageError(
            f"No freely-licensed Commons video matched {query!r}. "
            f"Public-domain archives contain what exists, not what was asked for.")

    pick = results[0]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(pick["url"], headers={
        "User-Agent": "NornPulse/1.0 (https://nornlabs.ai) footage-fetch",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, \
                open(out_path, "wb") as f:
            f.write(r.read())
    except Exception as e:
        out_path.unlink(missing_ok=True)
        raise FootageError(f"Could not download {pick['url']}: {e}") from e

    return Footage(
        path=out_path,
        source="public_domain",
        provider="wikimedia",
        description=pick["title"],
        licence=pick.get("licence"),
        attribution=pick.get("artist"),
        url=pick.get("descriptionurl"),
    )


def obtain(prompt: str, out_path: str | Path, prefer: str = "generated",
           **kwargs) -> Footage:
    """
    Get footage for a prompt from the preferred clean source.

    Generation is the default because it produces what was actually asked
    for, vertically, at the right length. The archive path is there for when
    generation is unavailable or unwanted, and it is allowed to fail loudly
    rather than silently substituting something unrelated.
    """
    if prefer == "generated":
        return generate_with_veo(prompt, out_path, **kwargs)
    if prefer == "public_domain":
        return fetch_wikimedia(prompt, out_path)
    raise ValueError(f"Unknown footage source {prefer!r}; "
                     f"expected 'generated' or 'public_domain'.")
