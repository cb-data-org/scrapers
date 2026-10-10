"""A polite HTTP client. It says who we are, follows robots.txt and Crawl-delay, spaces requests to each host
and stops at the first sign that a site doesn't want us (403/429). It never works around a block."""

import logging
import time
from collections.abc import Callable
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

USER_AGENT = "CervejaBarataBot/1.0 (+https://cervejabarata.com.br/bot)"

log = logging.getLogger(__name__)


class BlockedError(Exception):
    """The site refused us (403, 429, a CAPTCHA page…). Stop this source for the rest of the run."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"{reason} at {url}")
        self.url = url
        self.reason = reason


class DisallowedError(Exception):
    """robots.txt doesn't allow this URL for our user agent (or couldn't be read)."""


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class PoliteClient:
    def __init__(
        self,
        *,
        delay: float = 3.0,
        retries: int = 2,
        timeout: float = 20.0,
        user_agent: str = USER_AGENT,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.delay = delay
        self.retries = retries
        self.user_agent = user_agent
        self._clock = clock
        self._sleep = sleep
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._client = httpx.Client(
            headers={"User-Agent": user_agent},
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

    def get(self, url: str, *, robots: bool = True, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, robots=robots, **kwargs)

    def post(self, url: str, *, robots: bool = True, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, robots=robots, **kwargs)

    def request(
        self,
        method: str,
        url: str,
        *,
        robots: bool = True,
        retries: int | None = None,
        delay: float | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a request, retrying 5xx and network errors with backoff (`retries` overrides the client's).

        `delay` spaces requests to this host differently from the client's (a slower public API next to the
        source being scraped); a larger robots.txt Crawl-delay still wins. Pass `robots=False` only for
        official APIs called with our own credentials (e.g. the Instagram Graph API), never to get around a
        site's rules.
        """
        attempts = self.retries if retries is None else retries
        origin = _origin(url)
        wait = self.delay if delay is None else delay
        if robots:
            rules = self._rules(origin)
            if rules is None or not rules.can_fetch(self.user_agent, url):
                raise DisallowedError(url)
            crawl_delay = rules.crawl_delay(self.user_agent)
            if crawl_delay:
                wait = max(wait, float(crawl_delay))

        for attempt in range(attempts + 1):
            last_attempt = attempt == attempts
            self._space(origin, wait)
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.TransportError as error:
                if last_attempt:
                    raise
                log.warning("retrying %s after %s", url, error)
                self._sleep(wait * 2**attempt)
                continue
            if response.status_code in (403, 429):
                retry_after = response.headers.get("retry-after")
                hint = f" (Retry-After: {retry_after[:40]})" if retry_after else ""
                raise BlockedError(url, f"HTTP {response.status_code}{hint}")
            if response.status_code >= 500 and not last_attempt:
                log.warning("retrying %s after HTTP %s", url, response.status_code)
                self._sleep(wait * 2**attempt)
                continue
            response.raise_for_status()
            return response
        raise AssertionError("unreachable")

    def _space(self, origin: str, wait: float) -> None:
        last = self._last_request.get(origin)
        if last is not None:
            remaining = last + wait - self._clock()
            if remaining > 0:
                self._sleep(remaining)
        self._last_request[origin] = self._clock()

    def _rules(self, origin: str) -> RobotFileParser | None:
        if origin not in self._robots:
            self._robots[origin] = self._read_robots(origin)
        return self._robots[origin]

    def _read_robots(self, origin: str) -> RobotFileParser | None:
        """None means "don't crawl this host in this run" (robots.txt unreachable or rate-limited)."""
        rules = RobotFileParser(f"{origin}/robots.txt")
        self._space(origin, self.delay)
        try:
            response = self._client.get(f"{origin}/robots.txt")
        except httpx.TransportError as error:
            log.warning("robots.txt unreachable for %s: %s", origin, error)
            return None
        status = response.status_code
        if status in (401, 403):
            rules.disallow_all = True
        elif status == 429 or status >= 500:
            log.warning("robots.txt for %s answered HTTP %s; skipping the host this run", origin, status)
            return None
        elif status >= 400:
            rules.allow_all = True
        else:
            rules.parse(response.text.splitlines())
        return rules
