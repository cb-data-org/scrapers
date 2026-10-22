"""HTTP for the monitor's API calls: our User-Agent, a pause between two requests to one host, retries on 5xx
and network errors. Unlike the scrapers' PoliteClient it returns every response, refusals included, because an
API's 403 carries the reason (a service account that isn't a Search Console user, a wrong Bing key) and the
report should say it. Public web pages still go through PoliteClient, which reads robots.txt."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlsplit

import httpx

from ..core.http import USER_AGENT

log = logging.getLogger(__name__)


class ApiError(Exception):
    """An API answered with an error; the message is the API's own, shortened."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status


def error_message(response: httpx.Response) -> str:
    """The reason an API gives (Google, Bing, Cloudflare, Zenodo, plain text), at most 200 characters."""
    try:
        body: Any = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = None
    message: Any = ""
    if isinstance(body, dict):
        error, errors = body.get("error"), body.get("errors")
        if isinstance(error, dict):  # Google
            message = error.get("message") or error.get("status")
        elif body.get("error_description"):  # OAuth
            message = body["error_description"]
        elif body.get("Message"):  # Bing
            message = body["Message"]
        elif isinstance(errors, list) and errors:  # Cloudflare, GraphQL, Zenodo's field errors
            first = errors[0]
            message = first.get("message", first) if isinstance(first, dict) else first
        elif body.get("message"):  # our API, Zenodo
            message = body["message"]
        elif isinstance(error, str):
            message = error
    message = str(message or "") or response.text.strip() or response.reason_phrase
    return " ".join(message.split())[:200]


def check(response: httpx.Response) -> httpx.Response:
    """The response, or ApiError with the API's reason when it isn't a 2xx."""
    if not response.is_success:
        raise ApiError(response.status_code, error_message(response))
    return response


def describe(error: Exception) -> str:
    """A failure in words for the report: the API's reason, or the kind of network error (never a URL, which
    can carry a key)."""
    if isinstance(error, ApiError):
        return str(error)
    if isinstance(error, httpx.TimeoutException):
        return "timed out"
    if isinstance(error, httpx.HTTPError):
        return f"network error ({type(error).__name__})"
    if isinstance(error, (ValueError, KeyError, TypeError)):
        return f"unexpected answer ({type(error).__name__})"
    return type(error).__name__


class Api:
    def __init__(
        self,
        *,
        delay: float = 1.0,
        retries: int = 2,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.delay = delay
        self.retries = retries
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", url, **kwargs)

    def request(
        self, method: str, url: str, *, retries: int | None = None, delay: float | None = None, **kwargs: Any
    ) -> httpx.Response:
        """Send it, retrying 5xx answers and network errors with backoff; other answers come back as is."""
        host = urlsplit(url).netloc
        wait = self.delay if delay is None else delay
        attempts = self.retries if retries is None else retries
        for attempt in range(attempts + 1):
            last_attempt = attempt == attempts
            self._space(host, wait)
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.TransportError as error:
                if last_attempt:
                    raise
                log.warning("retrying %s %s after %s", method, urlsplit(url).path, type(error).__name__)
                self._sleep(max(wait, 1.0) * 2**attempt)
                continue
            if response.status_code >= 500 and not last_attempt:
                log.warning("retrying %s %s after HTTP %s", method, urlsplit(url).path, response.status_code)
                self._sleep(max(wait, 1.0) * 2**attempt)
                continue
            return response
        raise AssertionError("unreachable")

    def _space(self, host: str, wait: float) -> None:
        last = self._last.get(host)
        if last is not None:
            remaining = last + wait - self._clock()
            if remaining > 0:
                self._sleep(remaining)
        self._last[host] = self._clock()
