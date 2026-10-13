"""Which business email does a bar publish on its own website or link page? A check for the founding-bar
leads, run by hand on the public mirror's GitHub Actions (Source probe, probe=contacts).

    uv run python -m cb_scrapers.contact_probe --urls "bar.com.br,linktr.ee/bar" --out DIR --results FILE

The URLs come from the caller (the lead list's contact details stay behind the admin scope, which the mirror
doesn't hold). Each one is fetched with the polite client (robots.txt, our user agent, at least 3 s per host,
stopping at a 403/429 or a CAPTCHA page; never working around one), plus at most one same-site
contato/contact/sobre/about page linked from it. Emails come from mailto: links, the visible text (with simple
obfuscations such as "nome [at] dominio.com") and JSON-LD; images, trackers and placeholders are dropped.

The emails go only to the results file (a short-lived Actions artifact). The log and report.md, which land in
the public job summary, hold counts per URL and never an address.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from .core import BlockedError, DisallowedError, PoliteClient

log = logging.getLogger(__name__)

EMAIL = re.compile(
    r"[a-z0-9][a-z0-9._%+-]{0,63}@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}", re.I
)
#: "nome [at] dominio [dot] com", "nome(arroba)dominio.com", "nome arroba dominio.com.br" (a bare " at " is
#: ordinary English, so only bracketed or "arroba").
AT = re.compile(r"\s*(?:[\[({<]\s*(?:at|arroba)\s*[\])}>]|\sarroba\s)\s*", re.I)
DOT = re.compile(r"\s*[\[({<]\s*(?:dot|ponto)\s*[\])}>]\s*", re.I)
CONTACT_WORDS = re.compile(r"contato|contact|fale[\s_-]*conosco|sobre|about|quem[\s_-]*somos", re.I)
#: Pages that are a bot check rather than the site (Cloudflare, DataDome, PerimeterX).
CHALLENGE = re.compile(
    r"<title>\s*(?:just a moment|attention required|um momento)|cf-chl-|captcha-delivery\.com|px-captcha",
    re.I,
)
FILE_ENDINGS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".avif",
    ".ico",
    ".css",
    ".js",
    ".woff",
    ".woff2",
)
JUNK_DOMAINS = (
    "sentry.io",
    "sentry-next.wixpress.com",
    "wixpress.com",
    "wix.com",
    "example.com",
    "example.org",
    "example.net",
    "exemplo.com",
    "exemplo.com.br",
    "domain.com",
    "dominio.com",
    "dominio.com.br",
    "seudominio.com.br",
    "email.com",
    "seuemail.com",
)
JUNK_LOCALS = frozenset(
    {"seuemail", "seu-email", "seunome", "youremail", "your-email", "nome", "name", "usuario"}
)
SKIP_TEXT = frozenset({"script", "style", "noscript", "template", "svg"})


@dataclass(slots=True)
class Result:
    url: str
    #: found, none, blocked (403/429/CAPTCHA), disallowed (robots.txt), error.
    status: str = "none"
    emails: list[str] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    note: str = ""


class _Page(HTMLParser):
    """mailto: targets, links with their text, visible text and JSON-LD blocks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.mailtos: list[str] = []
        self.links: list[list[str]] = []
        self.text: list[str] = []
        self.ld: list[str] = []
        self._skip = 0
        self._in_ld = False
        self._link: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {key: value or "" for key, value in attrs}
        if tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_ld = True
        if tag in SKIP_TEXT:
            self._skip += 1
        elif tag == "a" and a.get("href"):
            href = a["href"].strip()
            if href.lower().startswith("mailto:"):
                self.mailtos.append(unquote(href[7:].split("?", 1)[0]))
            self._link = [href, a.get("title", "") + " " + a.get("aria-label", "")]
            self.links.append(self._link)
        elif tag in ("br", "p", "div", "li", "td", "h1", "h2", "h3", "h4"):
            self.text.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TEXT and self._skip:
            self._skip -= 1
        if tag == "script":
            self._in_ld = False
        if tag == "a":
            self._link = None

    def handle_data(self, data: str) -> None:
        if self._in_ld:
            self.ld.append(data)
        elif not self._skip:
            self.text.append(data)
            if self._link is not None:
                self._link[1] += data


def deobfuscate(text: str) -> str:
    return DOT.sub(".", AT.sub("@", text))


def plausible(email: str) -> bool:
    local, _, domain = email.partition("@")
    if not local or not domain or "@2x" in email or email.endswith(FILE_ENDINGS):
        return False
    if re.search(r"@\d+x\.", email) or re.fullmatch(r"[0-9a-f]{24,}", local):
        return False
    if local in JUNK_LOCALS:
        return False
    if "sentry" in domain:
        return False
    return not any(domain == junk or domain.endswith("." + junk) for junk in JUNK_DOMAINS)


def _emails_in(text: str) -> list[str]:
    return [match.group(0).strip(".").lower() for match in EMAIL.finditer(text)]


def extract_emails(html: str) -> list[str]:
    """The business emails a page shows, in order of first appearance, without images or placeholders."""
    page = _Page()
    page.feed(html)
    page.close()
    found: list[str] = []
    for mailto in page.mailtos:
        found += _emails_in(mailto.replace(",", " "))
    text = " ".join(page.text)
    found += _emails_in(text) + _emails_in(deobfuscate(text))
    for block in page.ld:
        found += _emails_in(block.replace("\\u0040", "@"))
    return [email for email in dict.fromkeys(found) if plausible(email)]


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def contact_link(html: str, base: str, scope: str | None = None) -> str | None:
    """The first same-site link to a contato/contact/sobre/about page, other than the page itself.

    `scope` is the URL we were given: when it has a path (a page on a shared host such as linktr.ee/bar or
    cozify.com.br/bar), the link must stay under that path, so the host's own "about" page never counts.
    """
    prefix = urlsplit(scope or base).path.rstrip("/")
    page = _Page()
    page.feed(html)
    page.close()
    here = urlsplit(base)._replace(fragment="").geturl()
    for href, text in page.links:
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        target = urljoin(base, href)
        parts = urlsplit(target)
        if parts.scheme not in ("http", "https") or _host(target) != _host(base):
            continue
        target = parts._replace(fragment="").geturl()
        if target == here or (prefix and not parts.path.startswith(prefix + "/")):
            continue
        if CONTACT_WORDS.search(parts.path) or CONTACT_WORDS.search(text):
            return target
    return None


def parse_urls(raw: str) -> list[str]:
    """Comma, space or newline separated; https:// is assumed when the scheme is missing."""
    urls = []
    for item in re.split(r"[,\s]+", raw.strip()):
        if not item:
            continue
        urls.append(item if re.match(r"https?://", item, re.I) else f"https://{item}")
    return list(dict.fromkeys(urls))


def _fetch(client: PoliteClient, url: str) -> tuple[str, str]:
    """The page's HTML and final URL; BlockedError on a bot-check page."""
    response = client.get(url)
    html = response.text
    if CHALLENGE.search(html[:20000]):
        raise BlockedError(url, "CAPTCHA or bot check")
    return html, str(response.url)


def probe(client: PoliteClient, url: str) -> Result:
    result = Result(url)
    try:
        html, final = _fetch(client, url)
        result.pages.append(final)
        emails = extract_emails(html)
        more = contact_link(html, final, url)
        if more:
            try:
                extra, extra_final = _fetch(client, more)
                result.pages.append(extra_final)
                emails += extract_emails(extra)
            except (BlockedError, DisallowedError, httpx.HTTPError) as error:
                result.note = f"contact page skipped: {type(error).__name__}"
    except BlockedError as error:
        result.status, result.note = "blocked", error.reason
        return result
    except DisallowedError:
        result.status, result.note = "disallowed", "robots.txt"
        return result
    except httpx.HTTPStatusError as error:
        result.status, result.note = "error", f"HTTP {error.response.status_code}"
        return result
    except httpx.HTTPError as error:
        result.status, result.note = "error", type(error).__name__
        return result
    result.emails = list(dict.fromkeys(emails))
    result.status = "found" if result.emails else "none"
    return result


def summary_line(result: Result) -> str:
    """One line for the public log: never an address."""
    if result.status == "found":
        outcome = f"found {len(result.emails)}"
    elif result.status == "none":
        outcome = "found 0"
    else:
        outcome = result.status
    pages = f"{len(result.pages)} page{'s' if len(result.pages) != 1 else ''}"
    note = f" ({result.note})" if result.note else ""
    return f"{result.url}: {outcome}, {pages}{note}"


def report(results: list[Result]) -> str:
    rows = ["# Contacts probe", "", "Emails are only in the results artifact (3 days), never here.", ""]
    rows += ["| URL | Result | Emails | Pages | Note |", "|---|---|---|---|---|"]
    for result in results:
        rows.append(
            f"| {result.url} | {result.status} | {len(result.emails)} | {len(result.pages)} | {result.note} |"
        )
    found = sum(1 for result in results if result.status == "found")
    rows += ["", f"{found} of {len(results)} URLs show at least one email."]
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--urls", required=True, help="comma-separated website or link-page URLs")
    parser.add_argument("--out", type=Path, required=True, help="directory for report.md (no emails)")
    parser.add_argument(
        "--results", type=Path, required=True, help="JSON file with the emails (keep private)"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    urls = parse_urls(args.urls)
    if not urls:
        print("no URLs given (the workflow's `urls` input)", file=sys.stderr)
        return 2
    results = []
    with PoliteClient(delay=3.0) as client:
        for url in urls:
            result = probe(client, url)
            results.append(result)
            print(summary_line(result), flush=True)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.md").write_text(report(results) + "\n")
    args.results.parent.mkdir(parents=True, exist_ok=True)
    args.results.write_text(json.dumps([asdict(result) for result in results], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
