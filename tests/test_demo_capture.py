"""
run_actions' job is to say WHEN a beat's content was actually confirmed
present, not just whether its actions finished — that timestamp is what lets
capture() trim lead-in by measurement instead of guessing from pixel
brightness (see demo_capture.capture, and the comment on FIRST_PAINT).

No real browser needed for this: a fake Playwright page is enough to check
the timing logic itself.
"""

import time

from demo_capture import run_actions


class _FakeLocator:
    def __init__(self, selector, fail):
        self.selector = selector
        self.fail = fail

    @property
    def first(self):
        return self

    def scroll_into_view_if_needed(self, timeout=None):
        if self.selector in self.fail:
            raise TimeoutError(self.selector)


class _FakeMouse:
    def move(self, x, y):
        pass

    def wheel(self, x, y):
        pass


class _FakePage:
    """fail: selectors that should behave as never-appearing."""

    def __init__(self, fail=()):
        self.fail = set(fail)
        self.mouse = _FakeMouse()

    def wait_for_selector(self, selector, timeout=None):
        if selector in self.fail:
            raise TimeoutError(selector)

    def click(self, selector, timeout=None):
        if selector in self.fail:
            raise TimeoutError(selector)

    def locator(self, selector):
        return _FakeLocator(selector, self.fail)


def test_no_confirming_action_returns_none():
    # wait/scroll are blind — neither confirms the page has real content.
    page = _FakePage()
    assert run_actions(page, [("wait", 0), ("scroll", 100)]) is None


def test_a_successful_settle_confirms_content():
    page = _FakePage()
    before = time.time()
    confirmed = run_actions(page, [("settle", "text=ready")])
    after = time.time()
    assert confirmed is not None
    assert before <= confirmed <= after


def test_a_failed_settle_does_not_confirm_content():
    page = _FakePage(fail={"text=never"})
    assert run_actions(page, [("settle", "text=never")]) is None


def test_confirmation_is_the_first_success_not_the_last():
    # A later wait must not push the timestamp later than when content
    # actually appeared — that's the whole point of measuring it here
    # instead of trimming by brightness after the fact.
    page = _FakePage()
    before = time.time()
    confirmed = run_actions(page, [("settle", "text=ready"), ("wait", 0.2)])
    assert confirmed - before < 0.1


def test_scroll_to_and_click_also_confirm_content():
    assert run_actions(_FakePage(), [("scroll_to", "x")]) is not None
    assert run_actions(_FakePage(), [("click", "x")]) is not None
