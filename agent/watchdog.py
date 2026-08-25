# agent/watchdog.py
"""
⚡ NornPulse: Pre-flight rights check (watchdog.py)
Norn Labs (nornlabs.ai)

Reads a brief before anything is generated or published, and says whether
it is about to reproduce someone else's property.

Why this exists rather than trusting the prompt: the brief writer is told
not to reference copyrighted characters, real people or brands, and that is
an instruction, not a control. Nothing verified it. Veo refuses the blatant
cases, but only after the call is paid for, and it will happily generate a
*near miss* — "a heavyset suburban father in a green shirt with a talking
dog" names nobody and is unmistakably somebody.

That risk is not hypothetical here. This project deliberately shows the
brief writer a channel's own best-performing titles as a voice reference,
and on the comedy channel those titles are almost entirely built on
copyrighted characters and real public figures. The model is asked to match
the energy without borrowing the property. This checks whether it did.

What it can and cannot do
-------------------------
It reliably catches things that are **named**: a character, a franchise, a
living person, a brand, a trademark.

It does **not** detect that generated footage resembles a protected work,
inspect anything a user uploads, or decide whether something is fair use,
parody or transformative. Those are judgements about the finished artefact
and about law, and a language model reading a paragraph of intent cannot
make either. Every verdict says which checks ran, so the gap is visible
instead of implied.

It is a filter, not a guarantee, and nothing here is legal advice.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MODEL = "gemini-3.6-flash"

PASS = "pass"
FLAG = "flag"
BLOCK = "block"

# What this check covers. Returned with every verdict so a caller can state
# what was actually examined rather than implying the whole problem was.
CHECKS_RUN = [
    "named real people",
    "named copyrighted characters and franchises",
    "brands, logos and trademarks",
    "song lyrics and quoted text",
]

# What it does not cover, at all. Stated for the same reason.
CHECKS_NOT_RUN = [
    "whether generated footage resembles a protected work",
    "the content of any uploaded video",
    "whether a use qualifies as fair use, parody or transformative",
]

# A cheap deterministic net under the model call. These are not a
# comprehensive list — no such list exists — but they are the specific
# properties this channel's own back catalogue reaches for, so they are the
# ones most likely to reappear in a voice-matched brief.
_HIGH_RISK = re.compile(
    r"\b("
    r"peter griffin|family guy|griffin family|stewie|brian griffin"
    r"|sopranos|tony soprano|ghostbusters|alf\b|shrek|minion[s]?"
    r"|mario|luigi|pokemon|pikachu|spongebob|simpsons|homer simpson"
    r"|batman|superman|spider-?man|marvel|disney|pixar|star wars"
    r"|mickey mouse|hello kitty|jurassic park|barbie"
    r"|jd vance|trump|biden|musk|elon"
    r")\b", re.I)


@dataclass
class Verdict:
    """The outcome of a rights check, with its own limits attached."""

    level: str
    reasons: List[str] = field(default_factory=list)
    checks_run: List[str] = field(default_factory=lambda: list(CHECKS_RUN))
    checks_not_run: List[str] = field(default_factory=lambda: list(CHECKS_NOT_RUN))
    checked_by: str = MODEL
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return self.level == BLOCK

    @property
    def needs_human(self) -> bool:
        return self.level in (BLOCK, FLAG)

    def summary(self) -> str:
        if self.level == PASS:
            return "no named third-party property found"
        return "; ".join(self.reasons) or self.level


_PROMPT = """You are a rights check on a short video before it is generated.

Your ONLY job is to find third-party property. You are not judging whether \
the idea is good, tasteful, or funny. Crude, absurd, and low-brow are all \
fine and are not your concern.

WHAT TO LOOK FOR
1. A real, identifiable living person — by name, or by description specific \
enough to identify one individual.
2. A copyrighted character, franchise, or setting — named, OR described \
closely enough that an ordinary viewer would recognise it. A "heavyset \
suburban father in a green shirt with a talking dog" names nobody and is \
unmistakably a specific show. Catch that.
3. A brand, logo, trademark, or product get-up.
4. Song lyrics or quoted text from a work.

VERDICTS
- "block": names or unmistakably depicts any of the above.
- "flag": genuinely borderline — evokes a genre or archetype closely enough \
that a person should look before it goes out. Generic archetypes (a knight, \
a pirate, a news anchor, a wizard) are NOT flags on their own.
- "pass": nothing of the above.

Be precise, not squeamish. Over-blocking generic material is a failure too: \
"a knight in a swamp" is a knight in a swamp.

MATERIAL
title: {title}
caption: {caption}
angle: {angle}
video prompt: {prompt}
tags: {tags}

Return ONLY JSON:
{{"verdict": "pass|flag|block",
  "reasons": ["<each specific finding, naming what it is and where>"],
  "offending_text": ["<the exact phrases responsible, if any>"]}}"""


def _json_from(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        start, end = text.find("{"), text.rfind("}")
        raw = text[start:end + 1] if start != -1 and end > start else None
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None


def check_text(title: str = "", caption: str = "", angle: str = "",
               prompt: str = "", tags: Optional[List[str]] = None,
               api_key: Optional[str] = None, model: str = MODEL) -> Verdict:
    """
    Check raw material for third-party property.

    Fails to FLAG rather than to PASS. If the check cannot run — no key, an
    API error, an unparseable answer — the honest state is "not checked",
    and treating that as approval would make the guard worse than useless by
    reporting a clean result it never obtained.
    """
    tags = tags or []
    blob = " ".join([title, caption, angle, prompt, " ".join(tags)])

    # Deterministic net first: it costs nothing and cannot be talked out of
    # a finding by the material it is reading.
    hits = sorted({m.group(0).lower() for m in _HIGH_RISK.finditer(blob)})
    if hits:
        return Verdict(
            level=BLOCK,
            reasons=[f"names known third-party property: {', '.join(hits)}"],
            checked_by="pattern list",
            raw={"pattern_hits": hits},
        )

    from agent import genai_client as gc

    # Vertex authenticates with application default credentials, so a
    # missing API key is only a failure when the call is going to AI
    # Studio. Treating it as one regardless would FLAG every check on
    # Vertex — turning "no third-party property found" into "could not
    # run" for reasons that have nothing to do with the material.
    key = api_key or os.getenv("GEMINI_API_KEY")
    if not gc.use_vertex() and not key:
        return Verdict(level=FLAG,
                       reasons=["rights check could not run: GEMINI_API_KEY is not set"],
                       checked_by="none")

    try:
        client, model = gc.client_for(model, api_key=key)
        response = client.models.generate_content(
            model=model,
            contents=_PROMPT.format(
                title=title or "(none)", caption=caption or "(none)",
                angle=angle or "(none)", prompt=prompt or "(none)",
                tags=", ".join(tags) or "(none)"),
        )
        data = _json_from(getattr(response, "text", "") or "")
    except Exception as e:
        logger.warning(f"Rights check failed to run: {e}")
        return Verdict(level=FLAG,
                       reasons=[f"rights check could not run: {str(e)[:120]}"],
                       checked_by="none")

    if not data:
        return Verdict(level=FLAG,
                       reasons=["rights check returned nothing readable"],
                       checked_by="none")

    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in (PASS, FLAG, BLOCK):
        return Verdict(level=FLAG,
                       reasons=[f"rights check returned an unknown verdict {verdict!r}"],
                       raw=data)

    return Verdict(
        level=verdict,
        reasons=[str(r) for r in (data.get("reasons") or [])],
        raw=data,
    )


def check_brief(brief, tags: Optional[List[str]] = None, **kwargs) -> Verdict:
    """Check a trend-loop Brief before its prompt is sent to a generator."""
    return check_text(
        title=getattr(brief, "title", ""),
        caption=getattr(brief, "caption", ""),
        angle=getattr(brief, "angle", ""),
        prompt=getattr(brief, "video_prompt", ""),
        tags=tags,
        **kwargs,
    )


def check_clip(clip: Dict[str, Any], tags: Optional[List[str]] = None,
               **kwargs) -> Verdict:
    """
    Check a clip about to be published.

    Metadata infringes on its own: a title like "Sopranos intro but cats"
    reproduces the property whether or not a single frame does.
    """
    return check_text(
        title=str(clip.get("hook_title") or ""),
        caption=str(clip.get("social_caption") or ""),
        angle=str(clip.get("angle") or ""),
        prompt=str(clip.get("video_prompt") or ""),
        tags=tags if tags is not None else list(clip.get("tags") or []),
        **kwargs,
    )


def describe(verdict: Verdict) -> str:
    """A short block for a terminal or an email, limits included."""
    icon = {PASS: "🟢", FLAG: "🟡", BLOCK: "🔴"}.get(verdict.level, "⚪")
    lines = [f"{icon} rights check: {verdict.level.upper()} — {verdict.summary()}"]
    for reason in verdict.reasons:
        lines.append(f"     · {reason}")
    lines.append(f"     checked: {', '.join(verdict.checks_run)}")
    lines.append(f"     NOT checked: {', '.join(verdict.checks_not_run)}")
    return "\n".join(lines)
