# Crawl4AI MCP server for Claude

A local stdio MCP server that gives Claude the web-crawling tools of
[Crawl4AI](https://github.com/unclecode/crawl4ai) (v0.9.6). No Docker needed.

## Tools

| Tool | What it does |
|---|---|
| `md` | Page → clean Markdown. `filter`: `fit` (default, strips boilerplate), `raw`, or `bm25` + `query` |
| `html` | Cleaned or raw HTML |
| `links` | Internal and external links on a page |
| `screenshot` | Full-page PNG (returned inline and saved to disk) |
| `pdf` | Renders the page to a PDF file |
| `execute_js` | Runs JS snippets in the page, then returns the results and the Markdown |
| `crawl` | Crawls up to 20 URLs concurrently |
| `deep_crawl` | Breadth-first crawl that follows links (depth ≤ 3, ≤ 50 pages) |

## Use in this repo (Claude Code)

`.mcp.json` registers the server, and `.claude/settings.json` pre-approves it.
Open Claude Code in this repo and run `/mcp`: `crawl4ai` should be listed. On
first launch, `run.sh` runs `setup.sh` automatically. To install ahead of time:

```bash
bash crawl4ai-mcp/setup.sh
```

## Use anywhere else (any project, Claude Desktop)

Claude Code, user scope (available in all projects):

```bash
claude mcp add crawl4ai --scope user -- bash /absolute/path/to/crawl4ai-mcp/run.sh
```

Claude Desktop: add this to `claude_desktop_config.json`:

```json
{ "mcpServers": { "crawl4ai": { "command": "bash", "args": ["/absolute/path/to/crawl4ai-mcp/run.sh"] } } }
```

## Configuration (env vars)

- `CRAWL4AI_PROXY`: proxy for the browser. Falls back to `HTTPS_PROXY`.
- `CRAWL4AI_MCP_MAX_CHARS`: default truncation limit (60000).
- `CRAWL4AI_MCP_OUTPUT_DIR`: where screenshots and PDFs are saved (default: system temp dir).

## Notes

- If `/opt/pw-browsers` exists (Claude Code cloud containers), `setup.sh` reuses
  that Chromium and pins Python `playwright` to the matching version. Otherwise it
  downloads Chromium.
- `mcp` is pinned `<2`: the server uses the v1 `FastMCP` API.
- Alternative: Crawl4AI's official Docker image serves MCP at
  `http://localhost:11235/mcp/sse`
  (`claude mcp add --transport sse c4ai-sse http://localhost:11235/mcp/sse`).
