"""Test doubles: a fake monotonic clock and a fake website."""

from collections.abc import Callable, Mapping

import httpx

Route = int | str | bytes | Callable[[httpx.Request], httpx.Response]


class FakeClock:
    """Monotonic clock whose sleep() just moves time forward and remembers how long it slept."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 3))
        self.now += seconds


class FakeSite:
    """A MockTransport handler serving routes by URL ("https://bar.test/robots.txt"); logs requests."""

    def __init__(self, routes: Mapping[str, Route]) -> None:
        self.routes = dict(routes)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = f"{request.url.scheme}://{request.url.host}{request.url.path}"
        route = self.routes.get(url, 404)
        if callable(route):
            return route(request)
        if isinstance(route, int):
            return httpx.Response(route)
        return httpx.Response(200, content=route.encode() if isinstance(route, str) else route)

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]
