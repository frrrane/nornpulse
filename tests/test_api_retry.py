"""
Tests for the transient-API retry policy shared by Bragi, Heimdall and
Mímir. The distinction that matters: a 503 "high demand" is worth
retrying, a 400 malformed request or a safety block is not — retrying
those just burns latency and quota to fail identically.
"""

import pytest

from agent.api_retry import is_transient, retry_on_transient


class _ApiError(Exception):
    def __init__(self, code, message=""):
        super().__init__(message or f"{code} error")
        self.code = code


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_server_and_ratelimit_codes_are_transient(code):
    assert is_transient(_ApiError(code))


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_client_errors_are_not_transient(code):
    assert not is_transient(_ApiError(code))


def test_the_real_heimdall_failure_is_classified_transient():
    """The exact error that lost all three thumbnails in a real batch run."""
    exc = RuntimeError(
        "503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is "
        "currently experiencing high demand. Spikes in demand are usually "
        "temporary. Please try again later.', 'status': 'UNAVAILABLE'}}"
    )
    assert is_transient(exc)


@pytest.mark.parametrize("message", [
    "Deadline exceeded",
    "Connection reset by peer",
    "Rate limit exceeded, please retry",
    "The service is temporarily unavailable",
    "Model is overloaded",
])
def test_transient_messages_without_a_status_code(message):
    assert is_transient(RuntimeError(message))


@pytest.mark.parametrize("message", [
    "Invalid argument: contents must not be empty",
    "API key not valid",
    "Response blocked by safety filters",
])
def test_permanent_messages_are_not_retried(message):
    assert not is_transient(RuntimeError(message))


# --------------------------------------------------------------------------
# Retry behaviour
# --------------------------------------------------------------------------

def test_retries_then_succeeds():
    calls = []

    @retry_on_transient(attempts=3)
    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise _ApiError(503, "high demand")
        return "ok"

    assert flaky() == "ok"
    assert len(calls) == 3


def test_gives_up_after_the_attempt_limit_and_reraises():
    """
    The caller's own except-branch must still see the exception so it can
    degrade gracefully (render the clip without the embellishment).
    """
    calls = []

    @retry_on_transient(attempts=2)
    def always_down():
        calls.append(1)
        raise _ApiError(503, "high demand")

    with pytest.raises(_ApiError):
        always_down()
    assert len(calls) == 2


def test_permanent_error_fails_fast_without_retrying():
    calls = []

    @retry_on_transient(attempts=3)
    def bad_request():
        calls.append(1)
        raise _ApiError(400, "Invalid argument")

    with pytest.raises(_ApiError):
        bad_request()
    assert len(calls) == 1, "a permanent error must not be retried"


def test_success_on_first_call_makes_exactly_one_call():
    calls = []

    @retry_on_transient()
    def fine():
        calls.append(1)
        return 42

    assert fine() == 42
    assert len(calls) == 1
