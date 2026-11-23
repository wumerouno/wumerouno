# Crawl4AI MCP server for Claude

An MCP server that gives Claude the web-crawling tools of
[Crawl4AI](https://github.com/unclecode/crawl4ai) (v0.9.6). It runs two ways:

| Where you want to use it | Mode | Needs hosting? |
|---|---|---|
| Claude Code in this repo (local or cloud sessions) | stdio, via `.mcp.json` | No |
| Claude Desktop app chat, on your computer | stdio, via `claude_desktop_config.json` | No |
| **claude.ai chat**: web, mobile, desktop, and every cloud Claude Code session | HTTP, as a **custom connector** | Yes, a public HTTPS URL |

## Tools

| Tool | What it does |
|---|---|
| `md` | Page → clean Markdown. `filter`: `fit` (default), `raw`, `bm25` + `query`. `citations=true` gives numbered ⟨n⟩ links plus a References list |
| `html` | Cleaned or raw HTML |
| `links` | Internal and external links on a page |
| `screenshot` | Full-page PNG, returned inline |
| `pdf` | Page as PDF (saved to disk locally, attached in HTTP mode) |
| `execute_js` | Runs JS snippets in the page, then returns the results and the Markdown |
| `crawl` | Up to 20 URLs concurrently |
| `deep_crawl` | Breadth-first crawl that follows links (depth ≤ 3, ≤ 50 pages) |

Every tool takes `stealth=true` for sites that block bots. This turns on Playwright
stealth, a random user agent, and simulated user behaviour.

## 1. Claude Code (this repo)

`.mcp.json` registers the server, and `.claude/settings.json` pre-approves it. Run
`/mcp` in Claude Code to check. The first launch installs automatically; to install
ahead of time, run `bash crawl4ai-mcp/setup.sh`. To make it available in every
project on your machine:

```bash
claude mcp add crawl4ai --scope user -- bash /absolute/path/to/crawl4ai-mcp/run.sh
```

## 2. Claude Desktop chat (local, no hosting)

One command, on macOS, Windows or Linux (needs Python 3.10+). It installs into
`~/.crawl4ai-mcp`, downloads Chromium, and adds `crawl4ai` to Claude Desktop's config
(your existing servers are kept, and a `.bak` backup is written).

macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/wumerouno/wumerouno/claude/busy-goodall-whwn6e/crawl4ai-mcp/install_desktop.py -o install_desktop.py && python3 install_desktop.py
```

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/wumerouno/wumerouno/claude/busy-goodall-whwn6e/crawl4ai-mcp/install_desktop.py -OutFile install_desktop.py; py install_desktop.py
```

Then quit Claude Desktop completely and reopen it. To remove it later:
`python3 install_desktop.py --uninstall`.

## 3. claude.ai chat everywhere (remote connector)

claude.ai can only reach MCP servers at a public HTTPS URL, so the server has to be
hosted somewhere.

1. **Make a secret token.** Anyone with the URL can use your crawler, so keep it private:
   `python3 -c 'import secrets;print(secrets.token_urlsafe(32))'`
2. **Deploy the Docker image** from `crawl4ai-mcp/Dockerfile` to any host that runs
   containers and gives you HTTPS: Render, Railway, Fly.io, Google Cloud Run, or a VPS
   behind Caddy or nginx. Settings:
   - environment: `MCP_AUTH_TOKEN=<your token>` (and optionally `CRAWL4AI_STEALTH=true`)
   - port: `8000` (or set `PORT`); health check: `/healthz`
   - memory: **at least 1 GB** (Chromium); on Docker, `--shm-size=1g`

   Local test: `docker build -t crawl4ai-mcp crawl4ai-mcp/ && docker run -p 8000:8000 -e MCP_AUTH_TOKEN=... --shm-size=1g crawl4ai-mcp`
3. **Add the connector** at <https://claude.ai/customize/connectors> → *Add custom
   connector*. Name it `Crawl4AI` and use this URL:

   ```
   https://<your-host>/<your-token>/mcp
   ```

   Leave the OAuth fields empty: the token in the path is the authentication.
4. In a chat, enable **Crawl4AI** from the tools/connectors menu and ask, e.g.
   *"Use Crawl4AI to summarise https://… with citations"*. The connector also shows
   up in the Claude mobile app, in Claude Desktop, and in new Claude Code cloud sessions.

Clients that can send headers can use `https://<host>/mcp` with
`Authorization: Bearer <token>` instead.

**Safety in HTTP mode:** the server refuses to crawl private, loopback and
link-local addresses, so it can't be used to probe the host's internal network
(override with `CRAWL4AI_MCP_ALLOW_PRIVATE=true`). It won't start without a token
of 24+ characters.

## Configuration (env vars)

| Variable | Default | Purpose |
|---|---|---|
| `MCP_AUTH_TOKEN` | (required for HTTP mode) | Secret for the connector URL / bearer header |
| `PORT` / `HOST` | `8000` / `0.0.0.0` | HTTP listen address |
| `CRAWL4AI_STEALTH` | `false` | Use stealth mode by default |
| `CRAWL4AI_PROXY` | `HTTPS_PROXY` | Proxy for the browser |
| `CRAWL4AI_MCP_MAX_CHARS` | `60000` | Default truncation limit |
| `CRAWL4AI_MCP_OUTPUT_DIR` | system temp dir | Where screenshots and PDFs are saved locally |

## Notes

- If `/opt/pw-browsers` exists (Claude Code cloud containers), `setup.sh` reuses that
  Chromium and pins Python `playwright` to the matching version.
- `mcp` is pinned `<2` (the server uses the v1 `FastMCP` API).
- Compared with [joedank/mcpcrawl4ai](https://github.com/joedank/mcpcrawl4ai): that
  project is a stdio proxy in front of the Crawl4AI Docker API. Its stealth mode and
  citation Markdown are included here. Its `ask_crawl4ai` documentation search depends
  on the Docker server's `/ask` endpoint, so it was left out.
