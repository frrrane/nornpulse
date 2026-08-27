"""
The demo, as data.

DEMO_SCRIPT.md is the human-readable version; this is the machine-readable
one, and they have to say the same thing. Each beat pairs the words Mímir
narrates with the screen actions Playwright performs while they are spoken.

Keeping them together is the whole point. The product will keep changing up
to the deadline, so a demo recorded by hand today is stale by next week and
nobody wants to re-record it. Defined this way, the demo is a build
artifact: re-run the capture the day before submitting and the footage
matches whatever the product is by then.

`narration` is what gets spoken. `actions` is a list of (verb, argument)
pairs run against a Playwright page. Verbs are deliberately few — anything
that needs more than these is a sign the demo is trying to show too much.
"""

from dataclasses import dataclass, field
from typing import Any, List, Tuple


@dataclass
class Beat:
    key: str
    narration: str
    page: str = "/"                     # path appended to the base URL
    actions: List[Tuple[str, Any]] = field(default_factory=list)
    # Seconds of video to keep. Narration length wins if it is longer, so
    # this is a floor rather than a cap — a beat is never cut mid-sentence.
    min_seconds: float = 4.0
    # A shot Playwright cannot take: a terminal, a finished Short playing,
    # an inbox. Declared rather than omitted, so the capture leaves a gap of
    # the right length and the shot list below stays honest about what still
    # has to be filmed by hand.
    manual: str = ""


# Verbs:
#   wait       seconds
#   scroll     pixels (positive is down) — for slow-drift shots where the
#              motion itself is the point, not a specific destination
#   scroll_to  CSS selector — for shots that need a specific element on
#              screen; fails loudly (a printed warning) if the layout moves
#              and the selector isn't found, instead of silently scrolling
#              the wrong distance
#   click      CSS selector
#   settle     selector to wait for before doing anything else
BEATS: List[Beat] = [
    Beat(
        key="hook",
        narration=(
            "Here are two numbers from the same dataset. Captions on a channel "
            "with a hundred thousand subscribers: plus thirty-four percent reach. "
            "The same captions on a channel with under a hundred subscribers: "
            "minus four percent. Same decision. Opposite answer."
        ),
        page="/",
        actions=[("settle", "[data-testid='stMetric']"), ("wait", 2.0)],
        min_seconds=16.0,
    ),
    Beat(
        key="problem",
        narration=(
            "Nearly every piece of short-form advice was measured on channels "
            "that already have an audience, then sold to channels that don't. At "
            "the wrong scale it doesn't just weaken. It reverses. Four and a half "
            "billion videos, banded by channel size."
        ),
        page="/",
        actions=[("wait", 1.0), ("scroll", 220), ("wait", 2.0), ("scroll", 220),
                 ("wait", 2.0)],
        min_seconds=14.0,
    ),
    Beat(
        key="generation",
        narration=(
            "So it makes videos on that basis. Urðr asks ClickHouse what is "
            "travelling and returns a topic with a denominator: unboxing, three "
            "trending videos, median nine point seven million views. Verðandi "
            "writes the brief, three beats in eight seconds, and picks one premise "
            "over two others. A rights check runs before a frame exists. Veo "
            "generates, Mímir narrates, the hook burns into the first three "
            "seconds. That clip is live, and nobody typed a word of it."
        ),
        page="/",
        manual=("Terminal: trend_publish.py --generate, showing topic, brief, "
                "rights verdict. Then the finished Short playing full-frame, "
                "sound on."),
        min_seconds=30.0,
    ),
    Beat(
        key="provenance",
        narration=(
            "And every decision is labelled by what it rests on — measured, "
            "assumed, or model judgement. The hook is measured, with a sample "
            "size attached. The framing is a seeded prior, because the public "
            "dataset has no visual features to ground it against, and it says so."
        ),
        page="/",
        # Renders inline now, not behind an expander — no click needed, and
        # clicking a summary that no longer exists is the 120s-timeout path
        # that used to run here. The heading text is the section's own copy
        # and doubles as "has this painted".
        actions=[("scroll_to", "text=How a clip gets decided"), ("wait", 3.0)],
        min_seconds=16.0,
    ),
    Beat(
        key="inward",
        narration=(
            "A tool that only audits other people's advice is doing half the job, "
            "so we pointed it at ourselves. The benchmark says a channel this size "
            "gets two and a half thousand views. Our two real channels get "
            "thirteen, and three hundred and forty-three."
        ),
        page="/page_intelligence",
        # The old pixel offset (260) was guessed against a page layout that
        # no longer exists — the real target is 1488px down a 6412px page,
        # measured against the live site. scroll_to fails loudly instead of
        # guessing again next time the page grows.
        actions=[("scroll_to", "text=Benchmark vs reality"), ("wait", 3.0)],
        min_seconds=17.0,
    ),
    Beat(
        key="why",
        narration=(
            "Here is why. The public dataset is a crawl, so it only contains "
            "videos discoverable enough to be crawled. A channel posting into the "
            "void isn't in it. Banding by size doesn't remove survivorship bias, "
            "because the population inside the band is filtered too."
        ),
        page="/page_intelligence",
        actions=[("scroll_to", "text=The public dataset is a crawl"), ("wait", 3.0)],
        min_seconds=16.0,
    ),
    Beat(
        key="scoreboard",
        narration=(
            "So forecasts are calibrated against the channel's own history, and then "
            "graded. Right now the scoreboard says two of sixteen are gradeable: three "
            "are too young, six were published before forecasts were recorded, and five "
            "point at videos that no longer exist. It reports that instead of averaging "
            "an accuracy figure over two clips."
        ),
        page="/page_intelligence",
        actions=[("scroll_to", "text=Forecast scoreboard"), ("wait", 3.0)],
        min_seconds=18.0,
    ),
    # The human gate is two shots, not one: the Review page is real evidence
    # Playwright can capture and re-capture on every run, so it's automated.
    # The inbox is a second inbox's UI and genuinely can't be — a screenshot
    # of a real reply requires a real reply. A single `manual` beat can't
    # hold both (a manual beat skips its own `actions` entirely), so this is
    # two beats, split where the automatable part actually ends: the specific
    # quoted comment ("could be funnier") is spoken while the inbox is on
    # screen, not the Review list, so the split falls before that line.
    Beat(
        key="gate_review",
        narration=(
            "Nothing publishes itself. Every clip goes to a human by email, with "
            "a comment that goes back into the record."
        ),
        page="/page_review",
        # Review has no <h1> — settle("h1") here silently ran the full
        # timeout every capture. This text is the page's own copy for the
        # warehouse-backed decision list, so it doubles as "the real ledger
        # has painted" and "goes back into the record" being visibly true.
        actions=[("settle", "text=read from ClickHouse"), ("wait", 2.0)],
        min_seconds=8.0,
    ),
    Beat(
        key="gate_inbox",
        narration=(
            "This one was approved with the note, could be funnier. The forecast "
            "is written down before publication, then graded against what "
            "happened. It can be wrong in public, which is the point."
        ),
        page="/page_review",
        manual="The approval email in a real inbox, reply comment “could be funnier” visible.",
        min_seconds=13.0,
    ),
    Beat(
        key="close",
        narration=(
            "Most AI tools present everything they output with identical "
            "confidence. This one tells you which parts it measured, which it "
            "assumed, and which it guessed, and when the sample is too thin it "
            "refuses to answer. NornPulse. Every chart is running against the "
            "real warehouse right now."
        ),
        page="/",
        actions=[("settle", "h1"), ("wait", 3.0)],
        min_seconds=16.0,
    ),
]


def total_narration_words() -> int:
    return sum(len(b.narration.split()) for b in BEATS)


def estimated_runtime_sec(words_per_minute: float = 155.0) -> float:
    """
    Rough spoken length. The Devpost cap is a hard three minutes, and going
    over is a disqualification risk rather than a style problem, so this is
    checked before anything is synthesised.
    """
    return 60.0 * total_narration_words() / words_per_minute
