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


# Verbs:
#   wait      seconds
#   scroll    pixels (positive is down)
#   click     CSS selector
#   settle    selector to wait for before doing anything else
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
            "Almost every piece of short-form advice you have been given was "
            "measured on channels that already have an audience. Then it gets sold "
            "to channels that don't. At the wrong scale it isn't just weaker. It "
            "reverses, because the thing that made it work, a subscriber base "
            "feeding browse traffic, doesn't exist yet."
        ),
        page="/",
        actions=[("wait", 1.0), ("scroll", 420), ("wait", 2.0), ("scroll", 420),
                 ("wait", 2.0)],
        min_seconds=18.0,
    ),
    Beat(
        key="provenance",
        narration=(
            "So every decision is labelled. Three measured, four assumed, one "
            "model judgement. The hook is measured, with a sample size attached. "
            "The framing is a seeded prior, because the public dataset has no "
            "visual features and there is nothing to ground it against. It says so "
            "rather than pretending."
        ),
        page="/",
        actions=[("scroll", 1100), ("wait", 1.5),
                 ("click", "[data-testid='stExpander'] summary"), ("wait", 3.0)],
        min_seconds=18.0,
    ),
    Beat(
        key="inward",
        narration=(
            "A tool that only audits other people's advice is doing half the job. "
            "So we pointed it at ourselves. The benchmark says a channel this size "
            "gets two and a half thousand views. Our real channels get thirteen, "
            "and three hundred and forty-three. The population median is roughly "
            "our best ever video, not a typical one."
        ),
        page="/page_intelligence",
        actions=[("settle", "[data-testid='stMetric']"), ("wait", 2.0),
                 ("scroll", 260), ("wait", 3.0)],
        min_seconds=20.0,
    ),
    Beat(
        key="why",
        narration=(
            "Here is why. The public dataset is a crawl. It only contains videos "
            "that were discoverable enough to be crawled. A channel posting into "
            "the void isn't in it. So banding by size doesn't remove survivorship "
            "bias, because the population inside the band is filtered too. "
            "Forecasts are calibrated against the channel's own history instead."
        ),
        page="/page_intelligence",
        actions=[("wait", 1.0), ("scroll", 300), ("wait", 3.0)],
        min_seconds=20.0,
    ),
    Beat(
        key="scoreboard",
        narration=(
            "And the forecast is written down before the clip publishes, then "
            "graded against what actually happened. Right now it says zero of "
            "thirteen are gradeable, and names every reason why. It would rather "
            "show you an empty panel than a percentage built on two clips."
        ),
        page="/page_intelligence",
        actions=[("wait", 1.0), ("scroll", 420), ("wait", 3.0)],
        min_seconds=16.0,
    ),
    Beat(
        key="close",
        narration=(
            "Most AI tools present everything they output with identical "
            "confidence. This one tells you which parts it measured, which it "
            "assumed, and which it guessed. And when the sample is too thin, it "
            "refuses to answer. NornPulse. It's live, and every chart on it is "
            "running against the real warehouse."
        ),
        page="/",
        actions=[("settle", "h1"), ("wait", 3.0)],
        min_seconds=17.0,
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
