"""
Stand ins for the venues' servers and for waiting, shared by the client tests as fixtures.
"""

import httpx
import pytest
from copytrader.api import http


@pytest.fixture
def slept(monkeypatch):
    """
    The waits the retries ask for, recorded rather than waited.
    """
    waits = []
    monkeypatch.setattr(http.time, "sleep", waits.append)
    return waits


@pytest.fixture
def serve(monkeypatch, slept):
    """
    A function that has every request answered by a handler, from an httpx.Request to an httpx.Response, rather than the
    network, and returns the list the requests sent are added to. The rate limiters let every request through at once.
    """
    monkeypatch.setattr(http.RateLimiter, "wait", lambda self, cost=1: None)

    def install(handler):
        sent = []

        def answer(request):
            sent.append(request)
            return handler(request)

        monkeypatch.setattr(http, "CLIENT", httpx.Client(transport=httpx.MockTransport(answer)))
        return sent

    return install
