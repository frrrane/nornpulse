# agent/secrets.py
"""
⚡ NornPulse: Secrets, from the environment or from Secret Manager (secrets.py)
Norn Labs (nornlabs.ai)

Four values in this project are genuinely secret — the ClickHouse password,
the Gemini API key, the Gmail app password, and the YouTube API key. Every
one is read through ``os.getenv`` from a ``.env`` file that is gitignored
and excluded from both the Docker and gcloud build contexts.

That works on a workstation and is wrong for a deployment. A container has
no ``.env``, so the values have to arrive some other way, and the usual
other way is baking them into an image or pasting them into a service
config — where they end up in build logs, in layer history, and in the
console for anyone with viewer access.

So on Google Cloud they come from Secret Manager instead.

Why this populates the environment rather than replacing os.getenv
------------------------------------------------------------------
Every call site already reads ``os.getenv``. Rewriting all of them would
mean touching modules that have nothing to do with secrets, days before a
deadline, to change where a string comes from. Loading into ``os.environ``
once at startup leaves every existing call working unchanged and keeps the
whole mechanism in one file that can be deleted if it turns out to be a bad
idea.

What it will not do
-------------------
It never overwrites a value already set. A developer with a ``.env``, or a
CI run with an injected variable, keeps what they have — a secrets loader
that silently substitutes a different credential than the one you set is a
debugging nightmare and a way to talk to the wrong database.

It never logs a value, only a name. And a missing secret is not an error
here: the caller that needs it already fails with its own message naming
what it wanted, which is more useful than this module guessing.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

logger = logging.getLogger("nornpulse.secrets")

# The values worth protecting. Everything else in .env is configuration —
# hostnames, ports, feature flags — and is not fetched.
SECRET_NAMES: List[str] = [
    "CLICKHOUSE_PASSWORD",
    "GEMINI_API_KEY",
    "GMAIL_APP_PASSWORD",
    "YOUTUBE_API_KEY",
]

# Secret Manager ids are lowercase-with-hyphens by convention, while the
# environment variables are the usual shouting. Mapped explicitly rather
# than transformed, so renaming one does not silently start reading a
# secret that does not exist.
SECRET_IDS: Dict[str, str] = {
    "CLICKHOUSE_PASSWORD": "clickhouse-password",
    "GEMINI_API_KEY": "gemini-api-key",
    "GMAIL_APP_PASSWORD": "gmail-app-password",
    "YOUTUBE_API_KEY": "youtube-api-key",
}


def project_id() -> Optional[str]:
    return (os.getenv("GOOGLE_CLOUD_PROJECT")
            or os.getenv("NORNPULSE_VERTEX_PROJECT")
            or None)


def enabled() -> bool:
    """
    Whether to consult Secret Manager at all.

    Off unless asked for. A workstation with a .env should not be making
    API calls to fetch values it already has, and a developer without
    credentials should not see a wall of permission errors on startup.
    """
    flag = os.getenv("NORNPULSE_USE_SECRET_MANAGER", "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def load_secrets(names: Optional[List[str]] = None,
                 project: Optional[str] = None) -> Dict[str, str]:
    """
    Fill in any missing secrets from Secret Manager.

    Returns a map of what it set, by name and *not* by value, so a caller
    can report which secrets came from where without ever holding one.
    """
    if not enabled():
        return {}

    target = project or project_id()
    if not target:
        logger.warning(
            "NORNPULSE_USE_SECRET_MANAGER is set but no project is configured; "
            "set GOOGLE_CLOUD_PROJECT. Falling back to the environment.")
        return {}

    try:
        from google.cloud import secretmanager
    except ImportError:
        logger.warning(
            "google-cloud-secret-manager is not installed; falling back to "
            "the environment.")
        return {}

    try:
        client = secretmanager.SecretManagerServiceClient()
    except Exception as e:
        logger.warning(f"Could not reach Secret Manager: {str(e)[:120]}")
        return {}

    loaded: Dict[str, str] = {}
    for name in (names or SECRET_NAMES):
        if os.getenv(name):
            # Already set. Never replaced: substituting a different
            # credential than the one a developer deliberately exported is
            # a way to quietly talk to the wrong database.
            continue

        secret_id = SECRET_IDS.get(name)
        if not secret_id:
            logger.warning(f"No Secret Manager id mapped for {name}; skipping.")
            continue

        path = f"projects/{target}/secrets/{secret_id}/versions/latest"
        try:
            response = client.access_secret_version(request={"name": path})
        except Exception as e:
            # Not fatal. Whatever needs this secret fails with its own
            # message naming what it wanted, which beats guessing here.
            logger.info(f"{name} not available from Secret Manager: {str(e)[:100]}")
            continue

        os.environ[name] = response.payload.data.decode("utf-8")
        loaded[name] = secret_id  # the id, never the value

    if loaded:
        logger.info(
            f"Loaded {len(loaded)} secret(s) from Secret Manager: "
            f"{', '.join(sorted(loaded))}")
    return loaded


def describe() -> str:
    """One line for a startup log, naming sources and never values."""
    if not enabled():
        return "secrets <- environment / .env"
    present = [n for n in SECRET_NAMES if os.getenv(n)]
    return (f"secrets <- Secret Manager (project {project_id()}), "
            f"{len(present)}/{len(SECRET_NAMES)} present")
