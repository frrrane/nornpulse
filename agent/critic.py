# agent/critic.py
"""
⚡ NornPulse: Pre-generation quality critic (critic.py)
Norn Labs (nornlabs.ai)

Argues with a brief in text, while that is still nearly free, instead of
finding out what is wrong with it after a paid Veo generation.

The pipeline already has three check-before-spending gates: the rights
`watchdog`, `trend_loop.brief_warnings`, and the provenance layer. This is
that same shape applied to quality rather than rights or provenance — same
"check the cheap thing before paying for the expensive thing" logic, same
verdict-with-limits pattern as watchdog.Verdict.

What makes this a critic rather than a rubber stamp: it is shown the
channel's actual rejection history — real comments from a human who
rejected a real finished clip — and asked to name the ONE specific thing
most likely to make a viewer scroll past this brief, not to emit a score
out of ten. A score is a number nobody can argue with; a named risk is a
claim that can be wrong, and checked against what actually happens to the
clip once it publishes.

The honest ceiling, stated once here rather than left implicit: a model
critiquing a model shares its blind spots. This would have caught a held-
pose ending or a mismatched title, because both are visible in the brief's
own text — and `trend_loop.brief_warnings` already catches those two
deterministically, cheaper and without a model call. What this adds on top
is judgement against the channel's own real failure patterns. It would NOT
catch "not funny at all", because the same taste that wrote the joke reads
this critique. Craft defects, not taste.
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
REVISE = "revise"
BLOCK = "block"

# How many of the most recent real rejections to show the critic. Not the
# whole ledger: an unbounded, ever-growing prompt would keep costing more
# per check while the marginal rejection ten months old teaches nothing an
# older one already did not.
HISTORY_LIMIT = 20


@dataclass
class Verdict:
    """
    The outcome of a quality critique, with its own limits attached — same
    shape as watchdog.Verdict, so a caller already familiar with one reads
    the other for free.
    """

    level: str
    scroll_risk: str = ""           # the ONE specific thing most likely to lose a viewer
    reasons: List[str] = field(default_factory=list)
    history_sample: int = 0         # how many real rejections this verdict was checked against
    checked_by: str = MODEL
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return self.level == BLOCK

    @property
    def needs_revision(self) -> bool:
        return self.level in (REVISE, BLOCK)

    def summary(self) -> str:
        if self.level == PASS:
            return "no pattern matching a past rejection found"
        return self.scroll_risk or "; ".join(self.reasons) or self.level


def rejection_history(limit: int = HISTORY_LIMIT) -> List[Dict[str, str]]:
    """
    Real reasons a human rejected a real finished clip — the training
    signal that makes this a critic rather than a rubber stamp.

    Reads the review ledger directly rather than requiring a caller to
    supply history, so the critique is always checked against whatever a
    reviewer most recently said, not a list frozen at prompt-writing time.
    """
    from agent import review_queue as rq
    rows = rq.list_decisions(status=rq.REJECTED)
    return [
        {"clip_id": r.get("clip_id", ""), "comment": (r.get("comment") or "").strip()}
        for r in rows[:limit]
        if (r.get("comment") or "").strip()
    ]


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


_PROMPT = """You are a blunt creative critic reviewing a short-video brief \
before it is generated. You are not the rights or fact checker — assume \
those are handled elsewhere. Your only job is craft: will this actually \
hold a viewer, or does it repeat a mistake this channel has already made \
and had a human reject for.

Do NOT emit a score out of ten. A number nobody can argue with is useless. \
Name the ONE specific thing most likely to make a viewer scroll past this, \
in concrete terms tied to this exact brief — not a generic note like "make \
it punchier".

REAL REJECTIONS THIS CHANNEL'S REVIEWER HAS ALREADY MADE (what to check \
this brief against — a brief that repeats one of these patterns should not \
pass):
{history}

VERDICTS
- "pass": no real pattern-match to a past rejection, no visible craft defect.
- "revise": a specific, fixable problem — say exactly what to change.
- "block": the brief is fundamentally not going to work as described (e.g. \
the premise cannot be shown in one 6-9 second clip, or it repeats a pattern \
that has been rejected outright more than once).

Default to "revise" when genuinely unsure — this check exists because \
generation is not free, so the safe failure is asking a human to look, not \
letting a weak brief through silently.

BRIEF
title: {title}
caption: {caption}
angle: {angle}
hook type: {hook_type}
video prompt: {prompt}

Return ONLY JSON:
{{"verdict": "pass|revise|block",
  "scroll_risk": "<the one specific thing most likely to make a viewer scroll, or empty if pass>",
  "reasons": ["<supporting point, tied to a specific past rejection if it applies>"]}}"""


def check_brief(brief, history: Optional[List[Dict[str, str]]] = None,
                api_key: Optional[str] = None, model: str = MODEL) -> Verdict:
    """
    Critique a trend-loop Brief before its prompt is sent to a generator.

    Fails to REVISE, not PASS: if the check cannot run — no key, an API
    error, an unparseable answer — the honest and safe state is "ask a
    human to look", matching the same "expensive to be wrong" reasoning
    watchdog.check_text uses for its own failure case (there it fails to
    FLAG for the same reason; here the cost being avoided is a full paid
    generation, not an unreviewed rights violation).
    """
    history = rejection_history() if history is None else history
    history_block = ("\n".join(f'  - "{h["comment"]}"' for h in history)
                      if history else "  (no rejection history recorded yet)")

    from agent import genai_client as gc

    key = api_key or os.getenv("GEMINI_API_KEY")
    if not gc.use_vertex() and not key:
        return Verdict(level=REVISE,
                       reasons=["quality critic could not run: GEMINI_API_KEY is not set"],
                       history_sample=len(history), checked_by="none")

    try:
        client, model = gc.client_for(model, api_key=key)
        response = client.models.generate_content(
            model=model,
            contents=_PROMPT.format(
                history=history_block,
                title=getattr(brief, "title", "") or "(none)",
                caption=getattr(brief, "caption", "") or "(none)",
                angle=getattr(brief, "angle", "") or "(none)",
                hook_type=getattr(brief, "hook_type", "") or "(none)",
                prompt=getattr(brief, "video_prompt", "") or "(none)",
            ),
        )
        data = _json_from(getattr(response, "text", "") or "")
    except Exception as e:
        logger.warning(f"Quality critique failed to run: {e}")
        return Verdict(level=REVISE,
                       reasons=[f"quality critic could not run: {str(e)[:120]}"],
                       history_sample=len(history), checked_by="none")

    if not data:
        return Verdict(level=REVISE,
                       reasons=["quality critic returned nothing readable"],
                       history_sample=len(history), checked_by="none")

    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in (PASS, REVISE, BLOCK):
        return Verdict(level=REVISE,
                       reasons=[f"quality critic returned an unknown verdict {verdict!r}"],
                       history_sample=len(history), raw=data)

    return Verdict(
        level=verdict,
        scroll_risk=str(data.get("scroll_risk", "")).strip(),
        reasons=[str(r) for r in (data.get("reasons") or [])],
        history_sample=len(history),
        raw=data,
    )


def critique_with_one_revision(channel, topics: List[Dict[str, Any]],
                               write_brief_fn=None, **write_kwargs):
    """
    Write a brief, critique it, and if it needs revision, try ONE fresh
    brief and critique that instead — then stop and hand back whatever the
    second attempt got, rather than looping.

    # ponytail: this "revision" is a fresh sample from write_brief, not the
    # critic's specific complaint fed back into the prompt — trend_loop's
    # write_brief has no parameter for arbitrary revision feedback today,
    # and adding one is a real change to a module this critic only calls.
    # A second independent sample already helps (write_brief proposes
    # several candidates and picks one each call, so a second call is not
    # the same coin flip twice) and keeps this addition self-contained.
    # Upgrade path: thread scroll_risk into write_brief's prompt as
    # explicit revision feedback if a second blind sample proves too weak
    # in practice.

    Returns (brief, verdict) — brief is None if write_brief itself declined
    (nothing trending suits the channel), in which case verdict is None too.
    """
    from agent import trend_loop as tl
    write_brief_fn = write_brief_fn or tl.write_brief

    brief = write_brief_fn(channel, topics, **write_kwargs)
    if brief is None:
        return None, None

    history = rejection_history()
    verdict = check_brief(brief, history=history)
    if not verdict.needs_revision:
        return brief, verdict

    logger.info(f"Critic asked for a revision ({verdict.summary()}); trying once more.")
    revised = write_brief_fn(channel, topics, **write_kwargs)
    if revised is None:
        return brief, verdict

    revised_verdict = check_brief(revised, history=history)
    return revised, revised_verdict


def describe(verdict: Verdict) -> str:
    """A short block for a terminal or an email, limits included."""
    icon = {PASS: "🟢", REVISE: "🟡", BLOCK: "🔴"}.get(verdict.level, "⚪")
    lines = [f"{icon} quality critic: {verdict.level.upper()} — {verdict.summary()}"]
    for reason in verdict.reasons:
        lines.append(f"     · {reason}")
    lines.append(f"     checked against {verdict.history_sample} real past rejection(s)")
    return "\n".join(lines)
