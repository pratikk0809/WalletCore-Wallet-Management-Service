"""Unit tests for the RateLimiter (a fake clock, so no sleeping)."""
import threading

import pytest

from app.core.rate_limit import RateLimiter
from app.exceptions import RateLimitedError

WINDOW = 60


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def limiter(clock):
    return RateLimiter(clock=clock)


def test_allows_up_to_the_limit_then_blocks(limiter):
    assert [limiter.hit("k", 3, WINDOW) for _ in range(3)] == [None, None, None]

    assert limiter.hit("k", 3, WINDOW) == WINDOW  # all three hit at the same second


def test_retry_after_counts_from_the_oldest_request_in_the_window(limiter, clock):
    limiter.hit("k", 2, WINDOW)          # t = 0
    clock.advance(10)
    limiter.hit("k", 2, WINDOW)          # t = 10
    clock.advance(10)                    # t = 20, window is full

    assert limiter.hit("k", 2, WINDOW) == 40  # the t=0 request expires at t=60


def test_requests_are_allowed_again_when_the_window_has_passed(limiter, clock):
    limiter.hit("k", 1, WINDOW)
    clock.advance(59.9)
    assert limiter.hit("k", 1, WINDOW) is not None  # still blocked

    clock.advance(0.1)                   # exactly one window after the first request
    assert limiter.hit("k", 1, WINDOW) is None


def test_keys_are_independent(limiter):
    limiter.hit("alice", 1, WINDOW)

    assert limiter.hit("alice", 1, WINDOW) is not None
    assert limiter.hit("bob", 1, WINDOW) is None


def test_blocked_requests_do_not_extend_the_block(limiter, clock):
    limiter.hit("k", 1, WINDOW)          # t = 0, allowed
    for _ in range(50):                  # hammering while blocked
        clock.advance(1)
        assert limiter.hit("k", 1, WINDOW) is not None

    clock.advance(10)                    # t = 60
    assert limiter.hit("k", 1, WINDOW) is None


def test_retry_after_is_rounded_up_and_never_below_one_second(limiter, clock):
    limiter.hit("k", 1, WINDOW)
    clock.advance(59.5)

    assert limiter.hit("k", 1, WINDOW) == 1


def test_expired_keys_are_swept_so_memory_does_not_grow(clock):
    limiter = RateLimiter(clock=clock, sweep_every=2)
    limiter.hit("old", 5, WINDOW)        # call 1
    clock.advance(100)

    limiter.hit("new", 5, WINDOW)        # call 2 triggers the sweep

    assert "old" not in limiter._hits
    assert "new" in limiter._hits


def test_reset_forgets_everything(limiter):
    limiter.hit("k", 1, WINDOW)

    limiter.reset()

    assert limiter.hit("k", 1, WINDOW) is None


def test_parallel_requests_never_exceed_the_limit(limiter):
    results = []

    def worker():
        results.append(limiter.hit("k", 10, WINDOW))

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(None) == 10     # exactly 10 allowed, 40 blocked


def test_rate_limited_error_carries_the_retry_after_header():
    error = RateLimitedError(retry_after=17)

    assert error.status_code == 429 and error.code == "RATE_LIMITED"
    assert error.headers == {"Retry-After": "17"}
    assert "17 seconds" in error.message