# agent/genai_client.py
"""
⚡ NornPulse: Choosing where model calls are billed (genai_client.py)
Norn Labs (nornlabs.ai)

One place that decides whether a call goes to Google AI Studio or to Vertex
AI, and under what name the model is known there.

Why this exists
---------------
The two surfaces serve the same models and bill from different wallets.
AI Studio draws on its own *prepayment credits*; Vertex bills the Google
Cloud billing account. Google Cloud credits — a coupon, a grant, trial
money — reach the second and cannot reach the first. When AI Studio's
prepay runs dry, a Cloud balance is no help, and the only route to spending
it is to make the same call against Vertex instead.

That is not a one-line switch, because the surfaces disagree about names
and about geography:

* Veo is ``veo-3.1-fast-generate-preview`` on AI Studio and
  ``veo-3.1-fast-generate-001`` on Vertex. Most other models keep their
  name.
* Vertex serves each model from particular locations, and they differ per
  model. ``gemini-3.6-flash`` answers only at ``global``; Veo answers at
  ``us-central1`` and not at ``global``; Lyria is the other way round.
  There is no single region that serves everything, so the location has to
  travel with the model rather than sitting in one setting.

A warning about probing availability
------------------------------------
``client.models.get()`` is free and reports the model *catalogue*, not what
a region will actually serve, and it has been wrong in both directions. It
reports ``gemini-3.6-flash`` as present in ``us-central1``, where a real
``generate_content`` returns 404. It reported Lyria as present in
``us-central1`` and absent from ``global``, and the real call said the
precise opposite. Only an actual call settles it, and for the generative
models an actual call costs money. The table below records what was
verified that way; anything inferred from the catalogue alone is marked,
because it is a guess, and the guesses have already been wrong.

Off by default. Nothing changes until NORNPULSE_USE_VERTEX is set, so this
can be committed and merged without touching how the pipeline behaves.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Vertex serves from one of these; which one depends on the model.
GLOBAL = "global"
US_CENTRAL = "us-central1"

DEFAULT_LOCATION = GLOBAL


@dataclass(frozen=True)
class Route:
    """Where one model lives on Vertex, and how sure we are."""

    model: str
    location: str
    # True when a real call to this model in this location has succeeded.
    # False means the name and region come from the catalogue, which has
    # already been observed to over-report.
    verified: bool = False


# Keyed by the AI Studio name the code already uses, so callers keep
# passing the name they always passed.
VERTEX_ROUTES: Dict[str, Route] = {
    # Verified by a real generate_content on 2026-08-25: answered at
    # global, 404 at us-central1 and europe-west4.
    "gemini-3.6-flash": Route("gemini-3.6-flash", GLOBAL, verified=True),
    "gemini-3.5-flash": Route("gemini-3.5-flash", GLOBAL, verified=True),
    "gemini-2.5-flash": Route("gemini-2.5-flash", GLOBAL, verified=True),

    # Verified on 2026-08-25: a real generation completed at us-central1.
    # The clip was then lost saving it — the save path went through the
    # Files API, which Vertex does not have — but the model name and region
    # are confirmed, which is what this table records.
    "veo-3.1-fast-generate-preview": Route(
        "veo-3.1-fast-generate-001", US_CENTRAL, verified=True),

    # Catalogue only: absent from global, present at us-central1 per
    # models.get, never confirmed by a real call. Kept at us-central1
    # because their verified sibling above answers there.
    "veo-3.1-lite-generate-preview": Route(
        "veo-3.1-lite-generate-001", US_CENTRAL),
    "veo-3.1-generate-preview": Route("veo-3.1-generate-001", US_CENTRAL),

    # Verified on 2026-08-25: Heimdall composed a real thumbnail at global.
    "gemini-3-pro-image": Route("gemini-3-pro-image", GLOBAL, verified=True),

    # Corrected on 2026-08-25 by being wrong in production, which is the
    # cautionary tale this whole table exists for. The catalogue said Lyria
    # was in us-central1 and NOT in global. The real call answered
    # "Unsupported location: us-central1. Supported locations are global,
    # us, and eu" — exactly backwards. Still unverified: this is the region
    # the error message named, not one a successful call has confirmed.
    "lyria-3-clip-preview": Route("lyria-3-clip-preview", GLOBAL),
    "lyria-3-pro-preview": Route("lyria-3-pro-preview", GLOBAL),

    # Catalogue only.
    "gemini-3.1-flash-tts-preview": Route(
        "gemini-3.1-flash-tts-preview", GLOBAL),
}


def use_vertex() -> bool:
    """Whether model calls should go to Vertex rather than AI Studio."""
    return os.getenv("NORNPULSE_USE_VERTEX", "").strip().lower() in (
        "1", "true", "yes", "on")


def vertex_project() -> Optional[str]:
    return (os.getenv("NORNPULSE_VERTEX_PROJECT")
            or os.getenv("GOOGLE_CLOUD_PROJECT")
            or None)


def route_for(model: str) -> Route:
    """
    How to address `model` on Vertex.

    An unknown model is passed through under its own name at the default
    location rather than raising: a new model that happens to work should
    not need an entry here first, and one that does not will fail at the
    call with Google's own error, which is more informative than ours.
    """
    known = VERTEX_ROUTES.get(model)
    if known:
        return known
    logger.info(
        f"No Vertex route recorded for {model!r}; trying it unchanged at "
        f"{DEFAULT_LOCATION}. Add it to VERTEX_ROUTES once a real call "
        f"confirms the name and region.")
    return Route(model, DEFAULT_LOCATION)


def client_for(model: str, api_key: Optional[str] = None) -> Tuple[Any, str]:
    """
    A client for this model, and the name to call it by.

    Returns both because on Vertex the two are entangled: the location is
    fixed when the client is built, and the right location depends on which
    model is being called. Callers keep passing their AI Studio model name
    and use whatever name comes back.
    """
    from google import genai

    if not use_vertex():
        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set.")
        return genai.Client(api_key=key), model

    project = vertex_project()
    if not project:
        raise RuntimeError(
            "NORNPULSE_USE_VERTEX is set but no project is configured. "
            "Set NORNPULSE_VERTEX_PROJECT or GOOGLE_CLOUD_PROJECT.")

    route = route_for(model)
    location = os.getenv("NORNPULSE_VERTEX_LOCATION") or route.location
    if not route.verified:
        logger.info(
            f"Vertex route for {model!r} -> {route.model!r} @ {location} is "
            f"from the model catalogue and has not been confirmed by a real "
            f"call. The catalogue over-reports: it lists models in regions "
            f"that return 404 on use.")
    return (genai.Client(vertexai=True, project=project, location=location),
            route.model)


def describe() -> str:
    """One line for a log or a CLI header, so the billing target is visible."""
    if not use_vertex():
        return "model calls -> Google AI Studio (prepay credits)"
    return (f"model calls -> Vertex AI, project {vertex_project()} "
            f"(Google Cloud billing)")
