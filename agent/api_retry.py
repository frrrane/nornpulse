# agent/api_retry.py
"""
⚡ NornPulse: Transient API retry policy
Norn Labs (nornlabs.ai)

Shared retry policy for the three creative composers — Bragi (Lyria),
Heimdall (image) and Mímir (TTS). Each already degrades gracefully when
its model call fails: the clip still renders, just without music, a
thumbnail or narration. That is the right behaviour for a PERMANENT
failure, but it silently throws away work on a TRANSIENT one.

Motivating incident: a real 3-video batch run lost all three thumbnails
to `503 UNAVAILABLE — "This model is currently experiencing high demand.
Spikes in demand are usually temporary. Please try again later."` The
API itself said the condition was temporary, and nothing retried.

Retry is deliberately limited to errors that can plausibly succeed on a
second attempt. Retrying a malformed request, an auth failure or a
safety block just burns latency and quota to fail identically, so those
still fail fast.
"""

import logging
import re

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger("nornpulse.api_retry")

# Server-side/capacity conditions worth a second attempt. 429 is included
# because Gemini uses it for rate limiting, which backoff is exactly the
# right response to.
_TRANSIENT_STATUS = (429, 500, 502, 503, 504)
_TRANSIENT_PATTERNS = re.compile(
    r"unavailable|overload|high demand|rate.?limit|quota|deadline|timeout|"
    r"temporar|try again|connection reset|broken pipe",
    re.IGNORECASE,
)


def is_transient(exc: BaseException) -> bool:
    """
    True when an exception looks retryable. The google-genai SDK surfaces
    errors in several shapes (typed APIError with .code, HTTP wrappers,
    plain RuntimeError with the status embedded in the message), so this
    checks a status attribute first and falls back to the message text.
    """
    for attr in ("code", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and value in _TRANSIENT_STATUS:
            return True

    text = str(exc)
    if any(str(code) in text for code in _TRANSIENT_STATUS):
        return True
    return bool(_TRANSIENT_PATTERNS.search(text))


def _log_retry(state) -> None:
    exc = state.outcome.exception() if state.outcome else None
    logger.warning(
        f"Transient API error (attempt {state.attempt_number}), retrying in "
        f"{getattr(state.next_action, 'sleep', 0):.1f}s: {str(exc)[:160]}"
    )


def retry_on_transient(attempts: int = 3):
    """
    Decorator: retry a model call on transient failures with exponential
    backoff, then give up and let the caller's own except-branch handle
    graceful degradation. Deliberately short — a clip should not stall
    for minutes because one optional embellishment is unavailable.
    """
    return retry(
        retry=retry_if_exception(is_transient),
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=1, min=2, max=12),
        before_sleep=_log_retry,
        reraise=True,
    )
