"""
Tests for the shared requests' retries and rate limiting, with a stand in for the server.
"""

import httpx
import json
import pytest
from copytrader.api import http

URL = "https://api.example.com/v1/trades"


class Spent:
    """
    A stand in for a rate limiter that records the tokens each try spends.
    """

    def __init__(self):
        self.costs = []

    def wait(self, cost=1):
        self.costs.append(cost)


def fake_clock(monkeypatch):
    """
    Stand in for the clock and for sleeping, so a sleep moves the clock on at once, and return the sleeps' lengths.
    """
    clock, waits = [0.0], []

    def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(http.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(http.time, "sleep", sleep)
    return waits


def test_the_rate_limiter_lets_a_burst_through_then_spaces_requests(monkeypatch):
    waits = fake_clock(monkeypatch)
    limiter = http.RateLimiter(2, burst=2)
    for _ in range(4):
        limiter.wait()
    assert waits == [0.5, 0.5]          # The first two go at once, then one every half second.


def test_a_weighted_request_waits_for_its_whole_weight_and_at_most_a_full_bucket(monkeypatch):
    waits = fake_clock(monkeypatch)
    limiter = http.RateLimiter(20, burst=1200)      # Hyperliquid's 1200 weight a minute.
    limiter.wait(1200)
    limiter.wait(20)
    limiter.wait(5000)
    assert waits == [pytest.approx(1.0), pytest.approx(60.0)]


def test_params_set_to_none_are_left_out_and_a_post_sends_its_body_as_json(serve):
    sent = serve(lambda request: httpx.Response(200, json={"ok": 1}))
    assert http.get_json(URL, {"user": "0xabc", "end": None, "takerOnly": False}) == {"ok": 1}
    http.post_json(URL, {"type": "meta"})
    assert dict(sent[0].url.params) == {"user": "0xabc", "takerOnly": "false"}
    assert sent[1].method == "POST" and json.loads(sent[1].content) == {"type": "meta"}


def test_a_429_is_retried_after_the_wait_the_server_asks_for(serve, slept):
    answers = iter([httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(200, json={"ok": 1})])
    serve(lambda request: next(answers))
    assert http.get_json(URL) == {"ok": 1}
    assert slept == [3.0]


def test_network_errors_are_retried_until_an_answer_comes(serve, slept):
    answers = [httpx.ConnectError("refused"), httpx.ConnectError("refused"), httpx.Response(200, json=[1])]

    def answer(request):
        next_answer = answers.pop(0)
        if isinstance(next_answer, Exception):
            raise next_answer
        return next_answer

    serve(answer)
    assert http.get_json(URL) == [1]
    assert len(slept) == 2


def test_an_error_status_raises_at_once_with_the_body(serve, slept):
    sent = serve(lambda request: httpx.Response(400, json={"error": "max historical activity offset of 5000 exceeded"}))
    with pytest.raises(http.RequestFailed) as failed:
        http.get_json(URL, {"offset": 5500})
    assert failed.value.status == 400 and "offset of 5000" in failed.value.body
    assert failed.value.url == URL + "?offset=5500"
    assert len(sent) == 1 and slept == []      # Never retried.


def test_a_server_error_that_outlasts_the_retries_raises(serve, slept):
    sent = serve(lambda request: httpx.Response(503))
    with pytest.raises(http.RequestFailed) as failed:
        http.get_json(URL)
    assert failed.value.status == 503
    assert len(sent) == http.RETRIES and len(slept) == http.RETRIES - 1


def test_a_wait_longer_than_max_retry_after_raises_rather_than_waits(serve, slept):
    serve(lambda request: httpx.Response(429, headers={"Retry-After": "7200"}))
    with pytest.raises(http.RequestFailed):
        http.get_json(URL)
    assert slept == []


def test_each_try_spends_the_requests_weight(serve):
    answers = iter([httpx.Response(500), httpx.Response(200, json={})])
    serve(lambda request: next(answers))
    limiter = Spent()
    http.post_json(URL, {}, limiter, cost=20)
    assert limiter.costs == [20, 20]
