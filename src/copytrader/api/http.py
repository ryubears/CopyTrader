"""
HTTP requests shared by the venue clients.

request() sends a GET, or a POST of a JSON body, through a host's rate limiter, and retries network errors, rate limit
answers, and server errors, waiting as long as the server asks when it says, so a client can page through a trader's
whole history without minding either. Any other error status raises RequestFailed with what the server sent. One httpx
client keeps connections open between calls, since a backfill makes thousands of calls to the same few hosts.
"""

import httpx
import random
import threading
import time

USER_AGENT = "CopyTrader research (github.com/ryubears/CopyTrader)"
TIMEOUT = 30            # Seconds a request may wait to connect, or between pieces of the answer.
RETRIES = 5             # Tries per request before giving up.
BACKOFF = 0.5           # Seconds before the first retry, doubling with each one after, up to MAX_BACKOFF.
MAX_BACKOFF = 30
MAX_RETRY_AFTER = 120   # The longest wait a server may ask for. Asked to wait longer, the request fails.
RETRY_STATUSES = {429, 500, 502, 503, 504}
CLIENT = httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})


class RequestFailed(Exception):
    """
    A request the server answered with an error status, with the status and the body it sent.
    """

    def __init__(self, status, body, url):
        super().__init__(f"HTTP {status} from {url}: {body[:300]}")
        self.status = status
        self.body = body
        self.url = url


class RateLimiter:
    """
    A token bucket for one host: lets a burst of requests through at once, then refills at rate tokens a second. A
    request spends one token, or its weight on a host that weighs its requests, as Hyperliquid does. Safe to use from
    threads.
    """

    def __init__(self, rate, burst=None):
        self.rate = rate                            # Tokens a second, on average.
        self.burst = burst or max(1, int(rate))     # Tokens that may be spent at once after a quiet spell.
        self.tokens = float(self.burst)
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def wait(self, cost=1):
        """
        Block until cost tokens are there, then spend them. A cost above burst waits for a full bucket.
        """
        cost = min(cost, self.burst)
        with self.lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= cost:
                    self.tokens -= cost
                    return
                time.sleep((cost - self.tokens) / self.rate)


def backoff(attempt):
    """
    Seconds to wait after failed try number attempt, counting from 1: BACKOFF doubled with each try up to MAX_BACKOFF,
    less up to half of it at random, so clients that failed together do not all retry together.
    """
    return min(MAX_BACKOFF, BACKOFF * 2 ** (attempt - 1)) * random.uniform(0.5, 1)


def retry_delay(response, attempt):
    """
    Seconds to wait before retrying a response with a status in RETRY_STATUSES: what its Retry-After header asks, or
    backoff() without one. None when the server asks for longer than MAX_RETRY_AFTER.
    """
    try:
        asked = float(response.headers["Retry-After"])
    except (KeyError, ValueError):      # No header, or the HTTP date form.
        return backoff(attempt)
    return asked if asked <= MAX_RETRY_AFTER else None


def request(method, url, params=None, body=None, limiter=None, cost=1):
    """
    Send a request through the limiter, spending cost tokens a try, and return the response. Params set to None are
    left out. Network errors and the statuses in RETRY_STATUSES are retried, RETRIES tries in all. Any other error
    status, or a retryable one that outlasts the tries, raises RequestFailed.
    """
    params = {k: v for k, v in params.items() if v is not None} if params else None
    for attempt in range(1, RETRIES + 1):
        if limiter:
            limiter.wait(cost)
        try:
            response = CLIENT.request(method, url, params=params, json=body)
        except httpx.TransportError:
            if attempt == RETRIES:
                raise
            time.sleep(backoff(attempt))
            continue
        if response.is_success:
            return response
        delay = retry_delay(response, attempt) if response.status_code in RETRY_STATUSES and attempt < RETRIES else None
        if delay is None:
            raise RequestFailed(response.status_code, response.text, str(response.request.url))
        time.sleep(delay)


def get_json(url, params=None, limiter=None):
    """
    GET a URL and parse the JSON body, see request().
    """
    return request("GET", url, params, limiter=limiter).json()


def post_json(url, body, limiter=None, cost=1):
    """
    POST a JSON body to a URL and parse the JSON answer, see request().
    """
    return request("POST", url, body=body, limiter=limiter, cost=cost).json()
