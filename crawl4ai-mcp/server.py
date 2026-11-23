"""Crawl4AI MCP server (stdio).

Exposes Crawl4AI (https://github.com/unclecode/crawl4ai) to Claude as MCP tools.
Mirrors the tools of Crawl4AI's official Docker-hosted MCP endpoint (md, html,
screenshot, pdf, execute_js, crawl) and adds a deep-crawl tool, but runs
locally over stdio, so no Docker daemon is needed.
"""

from __future__ import annotations

import base64
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from mcp.server.fastmcp import FastMCP, Image

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
)
from crawl4ai.content_filter_strategy import BM25ContentFilter
from crawl4ai.content_filter_strategy import PruningContentFilterLXML as PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

MAX_CHARS = int(os.environ.get("CRAWL4AI_MCP_MAX_CHARS", "60000"))
OUTPUT_DIR = Path(os.environ.get("CRAWL4AI_MCP_OUTPUT_DIR", tempfile.gettempdir())) / "crawl4ai-mcp"


def _browser_config() -> BrowserConfig:
    kwargs: dict = {"headless": True, "verbose": False}
    proxy = os.environ.get("CRAWL4AI_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        kwargs["proxy_config"] = {"server": proxy}
    return BrowserConfig(**kwargs)


_crawler: AsyncWebCrawler | None = None


async def _get_crawler() -> AsyncWebCrawler:
    """Start the browser lazily so the MCP handshake is instant."""
    global _crawler
    if _crawler is None:
        _crawler = AsyncWebCrawler(config=_browser_config())
        await _crawler.start()
    return _crawler


@asynccontextmanager
async def lifespan(_: FastMCP) -> AsyncIterator[None]:
    global _crawler
    try:
        yield
    finally:
        if _crawler is not None:
            await _crawler.close()
            _crawler = None


mcp = FastMCP("crawl4ai", lifespan=lifespan)


def _cache(bypass: bool) -> CacheMode:
    return CacheMode.BYPASS if bypass else CacheMode.ENABLED


def _truncate(text: str, limit: int = MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[... truncated {len(text) - limit} characters; raise max_chars to see more]"


async def _run(url: str, config: CrawlerRunConfig):
    crawler = await _get_crawler()
    result = await crawler.arun(url, config=config)
    if not result.success:
        raise RuntimeError(f"Crawl failed for {url}: {result.error_message}")
    return result


@mcp.tool()
async def md(
    url: str,
    filter: str = "fit",
    query: str | None = None,
    css_selector: str | None = None,
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

    config = CrawlerRunConfig(
        cache_mode=_cache(bypass_cache),
        css_selector=css_selector,
        markdown_generator=DefaultMarkdownGenerator(content_filter=content_filter),
    )
    result = await _run(url, config)
    markdown = result.markdown
    text = (markdown.fit_markdown if content_filter and markdown.fit_markdown else markdown.raw_markdown) or ""
    return _truncate(text, max_chars)


@mcp.tool()
async def html(url: str, cleaned: bool = True, bypass_cache: bool = False, max_chars: int = MAX_CHARS) -> str:
    """Return the HTML of a page (sanitised/cleaned by default, or raw with cleaned=False)."""
    result = await _run(url, CrawlerRunConfig(cache_mode=_cache(bypass_cache)))
    return _truncate((result.cleaned_html if cleaned else result.html) or "", max_chars)


@mcp.tool()
async def links(url: str, bypass_cache: bool = False) -> dict:
    """List the internal and external links found on a page."""
    result = await _run(url, CrawlerRunConfig(cache_mode=_cache(bypass_cache)))
    out = {}
    for kind in ("internal", "external"):
        out[kind] = [{"href": l.get("href"), "text": (l.get("text") or "").strip()[:200]} for l in result.links.get(kind, [])]
    return out


@mcp.tool()
async def screenshot(url: str, wait_seconds: float = 2.0, output_path: str | None = None) -> list:
    """Take a full-page PNG screenshot. Returns the image and the path it was saved to."""
    config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, screenshot=True, screenshot_wait_for=wait_seconds)
    result = await _run(url, config)
    if not result.screenshot:
        raise RuntimeError("No screenshot was produced")
    data = base64.b64decode(result.screenshot)
    path = Path(output_path) if output_path else OUTPUT_DIR / "screenshot.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return [Image(data=data, format="png"), f"Saved to {path}"]


@mcp.tool()
async def pdf(url: str, output_path: str | None = None) -> str:
    """Render a page to PDF and save it. Returns the saved file path."""
    result = await _run(url, CrawlerRunConfig(cache_mode=CacheMode.BYPASS, pdf=True))
    if not result.pdf:
        raise RuntimeError("No PDF was produced")
    path = Path(output_path) if output_path else OUTPUT_DIR / "page.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(result.pdf)
    return f"Saved PDF ({len(result.pdf)} bytes) to {path}"


@mcp.tool()
async def execute_js(url: str, scripts: list[str], max_chars: int = MAX_CHARS) -> dict:
    """Load a page, run JavaScript snippets in it (e.g. click 'load more'), then return the resulting Markdown.

    Each snippet runs in order in the page context. Return values of snippets are
    included under `js_results` when Crawl4AI reports them.
    """
    config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, js_code=scripts)
    result = await _run(url, config)
    return {
        "js_results": getattr(result, "js_execution_result", None),
        "markdown": _truncate(result.markdown.raw_markdown or "", max_chars),
    }


@mcp.tool()
async def crawl(urls: list[str], bypass_cache: bool = False, max_chars_per_page: int = 15000) -> list[dict]:
    """Crawl several URLs concurrently and return Markdown for each (max 20 URLs)."""
    if len(urls) > 20:
        raise ValueError("Pass at most 20 URLs per call")
    crawler = await _get_crawler()
    config = CrawlerRunConfig(
        cache_mode=_cache(bypass_cache),
        markdown_generator=DefaultMarkdownGenerator(content_filter=PruningContentFilter()),
    )
    results = await crawler.arun_many(urls, config=config)
    out = []
    for r in results:
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
    max_chars_per_page: int = 8000,
) -> list[dict]:
    """Breadth-first crawl starting at `url`, following links up to `max_depth` levels (max 50 pages)."""
    from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

    crawler = await _get_crawler()
    config = CrawlerRunConfig(
        cache_mode=CacheMode.ENABLED,
        deep_crawl_strategy=BFSDeepCrawlStrategy(
            max_depth=min(max_depth, 3),
            max_pages=min(max_pages, 50),
            include_external=include_external,
        ),
        markdown_generator=DefaultMarkdownGenerator(content_filter=PruningContentFilter()),
    )
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


if __name__ == "__main__":
    mcp.run()
