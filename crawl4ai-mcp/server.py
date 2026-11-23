"""Crawl4AI MCP server.

Exposes Crawl4AI (https://github.com/unclecode/crawl4ai) to Claude as MCP tools.
Mirrors the tools of Crawl4AI's official Docker-hosted MCP endpoint (md, html,
screenshot, pdf, execute_js, crawl), adds links/deep_crawl, plus stealth mode and
citation-style markdown (ideas borrowed from joedank/mcpcrawl4ai).

Two transports:
  python server.py          stdio, for Claude Code / Claude Desktop (local)
  python server.py --http   Streamable HTTP, for a claude.ai custom connector
                            (works in claude.ai web, mobile and desktop chat)
"""

from __future__ import annotations

import base64
import hmac
import ipaddress
import os
import socket
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import BlobResourceContents, EmbeddedResource

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from crawl4ai.content_filter_strategy import BM25ContentFilter
from crawl4ai.content_filter_strategy import PruningContentFilterLXML as PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

HTTP_MODE = "--http" in sys.argv or os.environ.get("MCP_TRANSPORT") == "http"
MAX_CHARS = int(os.environ.get("CRAWL4AI_MCP_MAX_CHARS", "60000"))
OUTPUT_DIR = Path(os.environ.get("CRAWL4AI_MCP_OUTPUT_DIR", tempfile.gettempdir())) / "crawl4ai-mcp"
DEFAULT_STEALTH = os.environ.get("CRAWL4AI_STEALTH", "false").lower() in ("1", "true", "yes")
# A public server must not be usable to probe the host's private network.
ALLOW_PRIVATE = os.environ.get("CRAWL4AI_MCP_ALLOW_PRIVATE", "false" if HTTP_MODE else "true").lower() in ("1", "true", "yes")


# ── browser management ──────────────────────────────────────────────────────

def _browser_config(stealth: bool) -> BrowserConfig:
    kwargs: dict = {"headless": True, "verbose": False, "enable_stealth": stealth}
    if stealth:
        kwargs["user_agent_mode"] = "random"
    proxy = os.environ.get("CRAWL4AI_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        kwargs["proxy_config"] = {"server": proxy}
    return BrowserConfig(**kwargs)


_crawlers: dict[bool, AsyncWebCrawler] = {}


async def _get_crawler(stealth: bool | None = None) -> AsyncWebCrawler:
    """Start browsers lazily (one normal, one stealth) so the MCP handshake is instant."""
    stealth = DEFAULT_STEALTH if stealth is None else stealth
    if stealth not in _crawlers:
        crawler = AsyncWebCrawler(config=_browser_config(stealth))
        await crawler.start()
        _crawlers[stealth] = crawler
    return _crawlers[stealth]


@asynccontextmanager
async def _stdio_lifespan(_: FastMCP) -> AsyncIterator[None]:
    try:
        yield
    finally:
        for crawler in _crawlers.values():
            await crawler.close()
        _crawlers.clear()


# In HTTP mode the lifespan runs per client session, so browsers are kept for the
# life of the process instead of being closed when one session ends.
mcp = FastMCP(
    "crawl4ai",
    lifespan=None if HTTP_MODE else _stdio_lifespan,
    host=os.environ.get("HOST", "0.0.0.0"),
    port=int(os.environ.get("PORT", "8000")),
    stateless_http=True,
)


# ── helpers ────────────────────────────────────────────────────────────────

def _check_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"Only absolute http(s) URLs are allowed: {url!r}")
    if ALLOW_PRIVATE:
        return
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as e:
        raise ValueError(f"Cannot resolve {parsed.hostname}: {e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError(f"Refusing to crawl private/internal address {ip} ({parsed.hostname})")


def _cache(bypass: bool) -> CacheMode:
    return CacheMode.BYPASS if bypass else CacheMode.ENABLED


def _truncate(text: str, limit: int = MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[... truncated {len(text) - limit} characters; raise max_chars to see more]"


def _anti_bot(config_kwargs: dict, stealth: bool | None) -> dict:
    if stealth if stealth is not None else DEFAULT_STEALTH:
        config_kwargs.update(magic=True, simulate_user=True, override_navigator=True)
    return config_kwargs


async def _run(url: str, stealth: bool | None, **config_kwargs):
    _check_url(url)
    crawler = await _get_crawler(stealth)
    result = await crawler.arun(url, config=CrawlerRunConfig(**_anti_bot(config_kwargs, stealth)))
    if not result.success:
        hint = "" if stealth else " (if the site blocks bots, retry with stealth=true)"
        raise RuntimeError(f"Crawl failed for {url}: {result.error_message}{hint}")
    return result


# ── tools ──────────────────────────────────────────────────────────────────

@mcp.tool()
async def md(
    url: str,
    filter: str = "fit",
    query: str | None = None,
    css_selector: str | None = None,
    citations: bool = False,
    stealth: bool | None = None,
    bypass_cache: bool = False,
    max_chars: int = MAX_CHARS,
) -> str:
    """Fetch a web page and return it as clean, LLM-ready Markdown.

    Args:
        url: Absolute http(s) URL to crawl.
        filter: "fit" (prune boilerplate, default), "raw" (full page markdown),
            or "bm25" (keep only passages relevant to `query`).
        query: Search query used when filter="bm25".
        css_selector: Optional CSS selector to restrict extraction (e.g. "article").
        citations: Replace inline links with numbered citations ⟨1⟩ and append a
            references list. Good for summaries that must cite sources.
        stealth: Use anti-bot-detection mode for sites that block crawlers.
        bypass_cache: Re-fetch instead of using Crawl4AI's cache.
        max_chars: Truncate output beyond this many characters.
    """
    if filter == "bm25":
        if not query:
            raise ValueError("filter='bm25' requires a query")
        content_filter = BM25ContentFilter(user_query=query)
    elif filter == "fit":
        content_filter = PruningContentFilter()
    elif filter == "raw":
        content_filter = None
    else:
        raise ValueError("filter must be 'fit', 'raw' or 'bm25'")

    result = await _run(
        url,
        stealth,
        # Crawl4AI's cache does not keep the citation fields, so always fetch fresh for them.
        cache_mode=_cache(bypass_cache or citations),
        css_selector=css_selector,
        markdown_generator=DefaultMarkdownGenerator(content_filter=content_filter),
    )
    m = result.markdown
    if citations:
        text = (m.markdown_with_citations or m.raw_markdown or "") + "\n\n" + (m.references_markdown or "")
    else:
        text = (m.fit_markdown if content_filter and m.fit_markdown else m.raw_markdown) or ""
    return _truncate(text, max_chars)


@mcp.tool()
async def html(
    url: str, cleaned: bool = True, stealth: bool | None = None, bypass_cache: bool = False, max_chars: int = MAX_CHARS
) -> str:
    """Return the HTML of a page (sanitised/cleaned by default, or raw with cleaned=False)."""
    result = await _run(url, stealth, cache_mode=_cache(bypass_cache))
    return _truncate((result.cleaned_html if cleaned else result.html) or "", max_chars)


@mcp.tool()
async def links(url: str, stealth: bool | None = None, bypass_cache: bool = False) -> dict:
    """List the internal and external links found on a page."""
    result = await _run(url, stealth, cache_mode=_cache(bypass_cache))
    return {
        kind: [{"href": l.get("href"), "text": (l.get("text") or "").strip()[:200]} for l in result.links.get(kind, [])]
        for kind in ("internal", "external")
    }


@mcp.tool()
async def screenshot(url: str, wait_seconds: float = 2.0, stealth: bool | None = None, output_path: str | None = None) -> list:
    """Take a full-page PNG screenshot. Returns the image (and, when run locally, the saved file path)."""
    result = await _run(url, stealth, cache_mode=CacheMode.BYPASS, screenshot=True, screenshot_wait_for=wait_seconds)
    if not result.screenshot:
        raise RuntimeError("No screenshot was produced")
    data = base64.b64decode(result.screenshot)
    out: list = [Image(data=data, format="png")]
    if not HTTP_MODE:
        path = Path(output_path) if output_path else OUTPUT_DIR / "screenshot.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        out.append(f"Saved to {path}")
    return out


@mcp.tool()
async def pdf(url: str, stealth: bool | None = None, output_path: str | None = None) -> list:
    """Render a page to PDF. Locally it is saved to disk; remotely it is returned as an attachment."""
    result = await _run(url, stealth, cache_mode=CacheMode.BYPASS, pdf=True)
    if not result.pdf:
        raise RuntimeError("No PDF was produced")
    if HTTP_MODE:
        return [
            f"PDF rendered ({len(result.pdf)} bytes).",
            EmbeddedResource(
                type="resource",
                resource=BlobResourceContents(
                    uri=f"file:///{urlparse(url).hostname or 'page'}.pdf",
                    mimeType="application/pdf",
                    blob=base64.b64encode(result.pdf).decode(),
                ),
            ),
        ]
    path = Path(output_path) if output_path else OUTPUT_DIR / "page.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(result.pdf)
    return [f"Saved PDF ({len(result.pdf)} bytes) to {path}"]


@mcp.tool()
async def execute_js(url: str, scripts: list[str], stealth: bool | None = None, max_chars: int = MAX_CHARS) -> dict:
    """Load a page, run JavaScript snippets in it (e.g. click 'load more'), then return the resulting Markdown.

    Snippets run in order in the page context; `return` values are reported under js_results.
    """
    result = await _run(url, stealth, cache_mode=CacheMode.BYPASS, js_code=scripts)
    return {
        "js_results": getattr(result, "js_execution_result", None),
        "markdown": _truncate(result.markdown.raw_markdown or "", max_chars),
    }


@mcp.tool()
async def crawl(
    urls: list[str], stealth: bool | None = None, bypass_cache: bool = False, max_chars_per_page: int = 15000
) -> list[dict]:
    """Crawl several URLs concurrently and return Markdown for each (max 20 URLs)."""
    if len(urls) > 20:
        raise ValueError("Pass at most 20 URLs per call")
    for u in urls:
        _check_url(u)
    crawler = await _get_crawler(stealth)
    config = CrawlerRunConfig(**_anti_bot({
        "cache_mode": _cache(bypass_cache),
        "markdown_generator": DefaultMarkdownGenerator(content_filter=PruningContentFilter()),
    }, stealth))
    out = []
    for r in await crawler.arun_many(urls, config=config):
        if r.success:
            text = r.markdown.fit_markdown or r.markdown.raw_markdown or ""
            out.append({"url": r.url, "success": True, "markdown": _truncate(text, max_chars_per_page)})
        else:
            out.append({"url": r.url, "success": False, "error": r.error_message})
    return out


@mcp.tool()
async def deep_crawl(
    url: str,
    max_depth: int = 1,
    max_pages: int = 10,
    include_external: bool = False,
    stealth: bool | None = None,
    max_chars_per_page: int = 8000,
) -> list[dict]:
    """Breadth-first crawl starting at `url`, following links up to `max_depth` levels (max 50 pages)."""
    from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

    _check_url(url)
    crawler = await _get_crawler(stealth)
    config = CrawlerRunConfig(**_anti_bot({
        "cache_mode": CacheMode.ENABLED,
        "deep_crawl_strategy": BFSDeepCrawlStrategy(
            max_depth=min(max_depth, 3),
            max_pages=min(max_pages, 50),
            include_external=include_external,
        ),
        "markdown_generator": DefaultMarkdownGenerator(content_filter=PruningContentFilter()),
    }, stealth))
    results = await crawler.arun(url, config=config)
    if not isinstance(results, list):
        results = [results]
    out = []
    for r in results:
        entry = {"url": r.url, "depth": (r.metadata or {}).get("depth"), "success": r.success}
        if r.success:
            entry["markdown"] = _truncate(r.markdown.fit_markdown or r.markdown.raw_markdown or "", max_chars_per_page)
        else:
            entry["error"] = r.error_message
        out.append(entry)
    return out


# ── HTTP transport with token auth ───────────────────────────────────────────

class TokenAuth:
    """ASGI middleware. Accepts either `Authorization: Bearer <token>` or the token as
    the first path segment (https://host/<token>/mcp), since claude.ai custom
    connectors take only a URL."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path == "/healthz":
            return await self._respond(send, 200, b"ok")
        prefix = f"/{self.token}"
        headers = dict(scope.get("headers") or [])
        bearer = headers.get(b"authorization", b"").decode()
        if path == prefix or path.startswith(prefix + "/"):
            scope = dict(scope, path=path[len(prefix):] or "/", raw_path=None)
        elif not hmac.compare_digest(bearer, f"Bearer {self.token}"):
            return await self._respond(send, 401, b"unauthorized")
        return await self.app(scope, receive, send)

    @staticmethod
    async def _respond(send, status: int, body: bytes):
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": body})


def run_http() -> None:
    import uvicorn

    token = os.environ.get("MCP_AUTH_TOKEN", "")
    if len(token) < 24:
        sys.exit("MCP_AUTH_TOKEN must be set to a random string of 24+ characters "
                 "(e.g. `python -c 'import secrets;print(secrets.token_urlsafe(32))'`).")
    app = TokenAuth(mcp.streamable_http_app(), token)
    uvicorn.run(app, host=mcp.settings.host, port=mcp.settings.port, log_level="info")


if __name__ == "__main__":
    if HTTP_MODE:
        run_http()
    else:
        mcp.run()
