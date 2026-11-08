"""A polite HTTP client. It says who we are, follows robots.txt and Crawl-delay, spaces requests to each host
and stops at the first sign that a site doesn't want us (403/429). It never works around a block.

To learn from our own requests what makes Preço da Hora refuse us, it can keep a log of them (`_RequestLog`):
set the environment variable CB_REQUEST_LOG to a file and every exchange with that site adds one JSON line to
it; no other host is logged. `cb requests report` reads those files."""

import hashlib
import html
import json
import logging
import os
import re
import time
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

USER_AGENT = "CervejaBarataBot/1.0 (+https://cervejabarata.com.br/bot)"

log = logging.getLogger(__name__)

#: The environment variable that switches the request log on: the JSON-lines file it appends to.
REQUEST_LOG_ENV = "CB_REQUEST_LOG"
#: The one host the request log keeps (the owner's call): Preço da Hora's, so its robots.txt, its session page
#: and its searches. Exchanges with any other host (CNPJ lookups, OpenStreetMap…) are never written.
REQUEST_LOG_HOST = "precodahora.ba.gov.br"
#: The fields of every line of the request log, in this order (`error` is null unless the exchange failed).
REQUEST_LOG_FIELDS = (
    "ts",
    "run",
    "host",
    "method",
    "path",
    "params",
    "label",
    "attempt",
    "status",
    "kind",
    "content_type",
    "bytes",
    "latency_ms",
    "retry_after",
    "rate_headers",
    "title",
    "body_sha256",
    "error",
)
#: How much of an answer the log looks at: for its <title> and for its fingerprint (a block page that comes
#: back again is recognised by it, without keeping the page).
BODY_SAMPLE = 4096
#: Statuses that mean "no" (kind "blocked").
BLOCK_STATUSES = (401, 403, 429)
#: The only response headers kept, to see a limit coming: the rate-limit families (x-ratelimit-*, and
#: ratelimit-limit, -remaining, -reset, -policy) and who answered, and when.
RATE_HEADER_PREFIXES = ("x-ratelimit", "ratelimit")
KEPT_HEADERS = ("server", "cf-ray", "date")
TITLE = re.compile(r"<title(?:\s[^>]*)?>(.*?)(?:</title\s*>|\Z)", re.IGNORECASE | re.DOTALL)


class BlockedError(Exception):
    """The site refused us (403, 429, a CAPTCHA page…). Stop this source for the rest of the run."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"{reason} at {url}")
        self.url = url
        self.reason = reason


class DisallowedError(Exception):
    """robots.txt doesn't allow this URL for our user agent (or couldn't be read)."""


class UnreachableError(DisallowedError):
    pass


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _one_line(text: str, limit: int) -> str:
    """Text for the log: control characters dropped (a page's <title> must not carry a terminal escape into
    the report), any whitespace one space, at most `limit` characters."""
    printable = "".join(
        " " if char.isspace() else char
        for char in text
        if char.isspace() or unicodedata.category(char) != "Cc"
    )
    return " ".join(printable.split())[:limit].rstrip()


def _kind(status: int | None, content_type: str, *, robots_txt: bool) -> str:
    """What came of an exchange: "robots" (a robots.txt fetch, whatever it answered), "blocked" (401, 403,
    429), "error" (any other 4xx or 5xx, a timeout, another failure of the transport), "json" or "html" by
    the content type, else "other" (a redirect too: the answer comes with the next exchange)."""
    if robots_txt:
        return "robots"
    if status is None:
        return "error"
    if status in BLOCK_STATUSES:
        return "blocked"
    if status >= 400:
        return "error"
    if status >= 300:
        return "other"
    content_type = content_type.lower()
    if "json" in content_type:
        return "json"
    if "text/html" in content_type:
        return "html"
    return "other"


def _title(body: bytes, encoding: str) -> str | None:
    """The first <title> in the first BODY_SAMPLE bytes of a page: on one line, at most 80 characters."""
    found = TITLE.search(body[:BODY_SAMPLE].decode(encoding, errors="replace"))
    return (_one_line(html.unescape(found.group(1)), 80) or None) if found else None


def _host(request: httpx.Request) -> str:
    return request.url.raw_host.decode("ascii", errors="replace")


def _param_names(request: httpx.Request) -> list[str]:
    """The names of a request's query and form parameters: never their values."""
    pairs = list(request.url.params.multi_items())
    if request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
        with suppress(httpx.RequestNotRead):
            pairs += httpx.QueryParams(request.content.decode("utf-8", errors="replace")).multi_items()
    names = (_one_line(name, 60) for name, _ in pairs)
    return list(dict.fromkeys(name for name in names if name))[:40]


def _kept_headers(headers: httpx.Headers) -> dict[str, str]:
    """The response headers the log keeps (RATE_HEADER_PREFIXES and KEPT_HEADERS), by lower-case name."""
    kept = {}
    for key, value in headers.items():
        name = key.lower()
        if name.startswith(RATE_HEADER_PREFIXES) or name in KEPT_HEADERS:
            kept[name] = _one_line(value, 200)
    return kept


def _answer(response: httpx.Response, *, robots_txt: bool) -> dict[str, Any]:
    """What the log says about an answer whose body has been read."""
    body = response.content
    content_type = response.headers.get("content-type")
    retry_after = response.headers.get("retry-after")
    return {
        "status": response.status_code,
        "kind": _kind(response.status_code, content_type or "", robots_txt=robots_txt),
        "content_type": _one_line(content_type, 100) if content_type else None,
        "bytes": len(body),
        "retry_after": _one_line(retry_after, 100) if retry_after else None,
        "rate_headers": _kept_headers(response.headers),
        "title": None if "json" in (content_type or "").lower() else _title(body, response.encoding),
        "body_sha256": hashlib.sha256(body[:BODY_SAMPLE]).hexdigest()[:16],
    }


@dataclass(slots=True)
class _Hop:
    """A request httpx sent for one call (a followed redirect makes a second one), and what came back."""

    request: httpx.Request
    at: datetime
    sent: float
    response: httpx.Response | None = None
    #: Milliseconds from the request being sent to the headers of the answer being read.
    latency_ms: int | None = None


class _RequestLog:
    """One JSON line per HTTP exchange with REQUEST_LOG_HOST, appended to a file (CB_REQUEST_LOG): when, to
    what (host, path, the NAMES of the query and form parameters), what came back (status, content type, size,
    the rate-limit headers, a page's <title> and a fingerprint of its first bytes) and how long it took. Never
    cookies, credentials, other headers, bodies, parameter values or the query string; and nothing at all
    about any other host. A call that follows a redirect makes an exchange of each request. Nothing here
    raises into the caller: a line that can't be written costs one warning."""

    def __init__(self, path: Path, run: str | None) -> None:
        self.path = path
        self.run = run
        self.hops: list[_Hop] = []

    @classmethod
    def from_env(cls) -> Self | None:
        """None (no log) unless CB_REQUEST_LOG names a file. The run is CB_RUN_ID, else GITHUB_RUN_ID."""
        path = os.environ.get(REQUEST_LOG_ENV)
        run = os.environ.get("CB_RUN_ID") or os.environ.get("GITHUB_RUN_ID") or None
        return cls(Path(path), run) if path else None

    def hooks(self) -> dict[str, list[Callable[..., None]]]:
        """httpx's event hooks: they see each request as it is sent and each answer as its headers arrive, so
        a latency is the server's, not the download's."""
        return {"request": [self.sending], "response": [self.answered]}

    def begin(self) -> None:
        """A new call to httpx starts: forget the requests of the last one."""
        self.hops = []

    def sending(self, request: httpx.Request) -> None:
        self.hops.append(_Hop(request, datetime.now(UTC), time.perf_counter()))

    def answered(self, response: httpx.Response) -> None:
        if self.hops:
            hop = self.hops[-1]
            hop.response = response
            hop.latency_ms = round((time.perf_counter() - hop.sent) * 1000)

    def record(
        self, label: str | None, attempt: int, *, robots_txt: bool, error: Exception | None = None
    ) -> None:
        """Write the lines for the call that just ended: each answer, or, when it raised `error`, its last
        request as a failure. Only requests to REQUEST_LOG_HOST are written (a redirect can lead out of it).
        The caller sees the answer or the error only after this."""
        hops, self.hops = self.hops, []
        try:
            # The request that failed is the call's last, on whichever host: never another one's.
            last = hops[-1] if hops else None
            lines = [
                self._line(hop, label, attempt, robots_txt, error if hop is last else None)
                for hop in hops
                if _host(hop.request) == REQUEST_LOG_HOST
            ]
            text = "".join(
                json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n" for line in lines
            )
            if text:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as out:
                    out.write(text)
        except Exception as failure:
            log.warning(
                "request log %s: a line was not written (%s: %s)", self.path, type(failure).__name__, failure
            )

    def _line(
        self, hop: _Hop, label: str | None, attempt: int, robots_txt: bool, error: Exception | None
    ) -> dict[str, Any]:
        request = hop.request
        response = None if error is not None else hop.response
        if response is None:
            # No answer (a timeout, another failure of the transport): the time is how long it took to fail.
            facts: dict[str, Any] = {
                "status": None,
                "kind": _kind(None, "", robots_txt=robots_txt),
                "content_type": None,
                "bytes": None,
                "latency_ms": round((time.perf_counter() - hop.sent) * 1000),
                "retry_after": None,
                "rate_headers": {},
                "title": None,
                "body_sha256": None,
            }
        else:
            facts = _answer(response, robots_txt=robots_txt) | {"latency_ms": hop.latency_ms}
        values = {
            "ts": hop.at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "run": self.run,
            "host": _host(request),
            "method": request.method,
            "path": request.url.raw_path.partition(b"?")[0].decode("ascii", errors="replace"),
            "params": _param_names(request),
            "label": (_one_line(label, 200) or None) if label else None,
            "attempt": attempt,
            "error": None if error is None else type(error).__name__,
        } | facts
        return {field: values[field] for field in REQUEST_LOG_FIELDS}


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
        self._request_log = _RequestLog.from_env()
        self._client = httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
            event_hooks=self._request_log.hooks() if self._request_log is not None else None,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get(
        self, url: str, *, robots: bool = True, label: str | None = None, **kwargs: Any
    ) -> httpx.Response:
        return self.request("GET", url, robots=robots, label=label, **kwargs)

    def post(
        self, url: str, *, robots: bool = True, label: str | None = None, **kwargs: Any
    ) -> httpx.Response:
        return self.request("POST", url, robots=robots, label=label, **kwargs)

    def request(
        self,
        method: str,
        url: str,
        *,
        robots: bool = True,
        retries: int | None = None,
        delay: float | None = None,
        label: str | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a request, retrying 5xx and network errors with backoff (`retries` overrides the client's).

        `delay` spaces requests to this host differently from the client's (a slower public API next to the
        source being scraped); a larger robots.txt Crawl-delay still wins. Pass `robots=False` only for
        official APIs called with our own credentials (e.g. the Instagram Graph API), never to get around a
        site's rules. `label` names the call in the request log (which search it was), nothing else.
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
                with self._logged(label, attempt + 1):
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

    @contextmanager
    def _logged(
        self, label: str | None = None, attempt: int = 1, *, robots_txt: bool = False
    ) -> Iterator[None]:
        """Around one call to httpx: when it ends, the request log gets its line(s), an answer or a failure
        (which goes on to the caller), before anything is raised about what the answer says. Without a log
        it is no more than a pass."""
        request_log = self._request_log
        if request_log is None:
            yield
            return
        request_log.begin()
        try:
            yield
        except Exception as error:
            request_log.record(label, attempt, robots_txt=robots_txt, error=error)
            raise
        request_log.record(label, attempt, robots_txt=robots_txt)

    def _space(self, origin: str, wait: float) -> None:
        last = self._last_request.get(origin)
        if last is not None:
            remaining = last + wait - self._clock()
            if remaining > 0:
                self._sleep(remaining)
        self._last_request[origin] = self._clock()

    def _rules(self, origin: str) -> RobotFileParser | None:
        if origin not in self._robots:
            try:
                self._robots[origin] = self._read_robots(origin)
            except UnreachableError:
                self._robots[origin] = None
                raise
        return self._robots[origin]

    def _read_robots(self, origin: str) -> RobotFileParser | None:
        """None means "don't crawl this host in this run" (robots.txt rate-limited or erroring)."""
        rules = RobotFileParser(f"{origin}/robots.txt")
        self._space(origin, self.delay)
        try:
            with self._logged(robots_txt=True):
                response = self._client.get(f"{origin}/robots.txt")
        except httpx.TransportError as error:
            log.warning("robots.txt unreachable for %s: %s", origin, error)
            raise UnreachableError(origin) from error
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
