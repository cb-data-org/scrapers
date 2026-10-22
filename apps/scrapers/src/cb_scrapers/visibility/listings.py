"""Where AI agents find the MCP server (https://cervejabarata.com.br/mcp): the official MCP Registry (Glama,
PulseMCP, GitHub's registry and VS Code read it), Glama's connector page and Smithery's public registry.
Public pages and APIs only, no account: the registry and Smithery through their search APIs, Glama's page
through the polite client (robots.txt)."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from ..core import BlockedError, DisallowedError, PoliteClient
from .http import Api, ApiError, check, describe
from .site import HOST

#: The server's name in the registry (server.json at the repository root).
SERVER = "br.com.cervejabarata/mcp"
REGISTRY = "https://registry.modelcontextprotocol.io/v0.1/servers"
OFFICIAL = "io.modelcontextprotocol.registry/official"
GLAMA = f"https://glama.ai/mcp/connectors/{SERVER}"
SMITHERY = "https://registry.smithery.ai/servers"


@dataclass(slots=True)
class Listing:
    name: str
    #: yes, no, or unknown (the check failed; the note says why).
    listed: str = "no"
    url: str = ""
    note: str = ""


@dataclass(slots=True)
class Listings:
    status: str = "ok"
    note: str = ""
    items: list[Listing] = field(default_factory=list)

    @property
    def found(self) -> int:
        return sum(item.listed == "yes" for item in self.items)


def registry(api: Api) -> Listing:
    item = Listing("MCP Registry", url=f"{REGISTRY}?search={SERVER}")
    try:
        servers = check(api.get(REGISTRY, params={"search": SERVER})).json().get("servers", [])
    except (ApiError, httpx.HTTPError, ValueError, AttributeError) as error:
        item.listed, item.note = "unknown", describe(error)
        return item
    for entry in servers if isinstance(servers, list) else []:
        if not isinstance(entry, dict):
            continue
        server, meta = entry.get("server") or {}, (entry.get("_meta") or {}).get(OFFICIAL) or {}
        if server.get("name") != SERVER or not meta.get("isLatest", True):
            continue
        status = meta.get("status", "active")
        item.listed = "yes" if status == "active" else "no"
        item.note = f"version {server.get('version', '?')}" + ("" if status == "active" else f", {status}")
        break
    return item


def glama(client: PoliteClient) -> Listing:
    item = Listing("Glama", url=GLAMA)
    try:
        client.get(GLAMA)
        item.listed = "yes"
    except httpx.HTTPStatusError as error:
        if error.response.status_code in (404, 410):
            return item
        item.listed, item.note = "unknown", f"HTTP {error.response.status_code}"
    except DisallowedError:
        item.listed, item.note = "unknown", "robots.txt disallows the page"
    except BlockedError as error:
        item.listed, item.note = "unknown", error.reason
    except httpx.HTTPError as error:
        item.listed, item.note = "unknown", describe(error)
    return item


def _ours(server: dict[str, object]) -> bool:
    homepage = urlsplit(str(server.get("homepage") or "")).hostname or ""
    return (
        homepage == HOST
        or homepage.endswith(f".{HOST}")
        or "cervejabarata" in str(server.get("qualifiedName") or "").lower()
    )


def smithery(api: Api) -> Listing:
    item = Listing("Smithery", url=f"{SMITHERY}?q=cervejabarata")
    try:
        servers = check(api.get(SMITHERY, params={"q": "cervejabarata"})).json().get("servers", [])
    except (ApiError, httpx.HTTPError, ValueError, AttributeError) as error:
        item.listed, item.note = "unknown", describe(error)
        return item
    match = next((server for server in servers if isinstance(server, dict) and _ours(server)), None)
    if match:
        item.listed = "yes"
        item.url = f"https://smithery.ai/server/{match.get('qualifiedName', '')}"
    return item


def collect(api: Api, client: PoliteClient) -> Listings:
    result = Listings(items=[registry(api), glama(client), smithery(api)])
    unknown = [item.name for item in result.items if item.listed == "unknown"]
    if unknown:
        result.status, result.note = "partial", f"couldn't check {', '.join(unknown)}"
    return result
