#!/usr/bin/env python3
"""One-command installer: adds the Crawl4AI MCP server to the Claude Desktop app.

Works on macOS, Windows and Linux. Needs Python 3.10+ and an internet connection.

    python3 install_desktop.py            # install / update
    python3 install_desktop.py --uninstall

What it does:
  1. Copies server.py + requirements.txt into ~/.crawl4ai-mcp (downloads them
     from GitHub if this script was downloaded on its own).
  2. Creates a virtualenv there and installs crawl4ai, mcp and Chromium.
  3. Adds a "crawl4ai" entry to claude_desktop_config.json (a .bak backup of the
     previous file is kept). Other MCP servers in the file are left untouched.
Then quit and reopen Claude Desktop.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import venv
from pathlib import Path

REPO_RAW = "https://raw.githubusercontent.com/wumerouno/wumerouno"
REF = os.environ.get("CRAWL4AI_MCP_REF", "claude/busy-goodall-whwn6e")
FILES = ("server.py", "requirements.txt")
INSTALL_DIR = Path(os.environ.get("CRAWL4AI_MCP_HOME", Path.home() / ".crawl4ai-mcp"))
SERVER_NAME = "crawl4ai"


def say(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def config_path() -> Path:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if system == "Windows":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Claude/claude_desktop_config.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Claude/claude_desktop_config.json"


def venv_python() -> Path:
    sub = "Scripts/python.exe" if platform.system() == "Windows" else "bin/python"
    return INSTALL_DIR / ".venv" / sub


def fetch_sources() -> None:
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).resolve().parent
    for name in FILES:
        local = here / name
        if local.exists() and local.resolve() != (INSTALL_DIR / name).resolve():
            shutil.copy2(local, INSTALL_DIR / name)
        elif not local.exists():
            url = f"{REPO_RAW}/{REF}/crawl4ai-mcp/{name}"
            say(f"Downloading {url}")
            with urllib.request.urlopen(url, timeout=60) as r:
                (INSTALL_DIR / name).write_bytes(r.read())


def run(cmd: list, **kw) -> None:
    say(" ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def install_python_env() -> None:
    py = venv_python()
    if not py.exists():
        say(f"Creating virtualenv in {INSTALL_DIR / '.venv'}")
        venv.EnvBuilder(with_pip=True, clear=False).create(INSTALL_DIR / ".venv")
    run([py, "-m", "pip", "install", "--upgrade", "pip"])
    run([py, "-m", "pip", "install", "-r", INSTALL_DIR / "requirements.txt"])
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(INSTALL_DIR / "browsers"))
    run([py, "-m", "playwright", "install", "chromium"], env=env)


def update_config(remove: bool = False) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if path.exists() and path.read_text(encoding="utf-8").strip():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            sys.exit(f"{path} is not valid JSON. Fix or move it, then run this again.")
        shutil.copy2(path, path.with_suffix(".json.bak"))
    servers = data.setdefault("mcpServers", {})
    if remove:
        servers.pop(SERVER_NAME, None)
    else:
        servers[SERVER_NAME] = {
            "command": str(venv_python()),
            "args": [str(INSTALL_DIR / "server.py")],
            "env": {
                "PLAYWRIGHT_BROWSERS_PATH": str(INSTALL_DIR / "browsers"),
                "CRAWL4_AI_BASE_DIRECTORY": str(INSTALL_DIR),
                "CRAWL4AI_MCP_OUTPUT_DIR": str(Path.home() / "Downloads"),
                "PYTHONIOENCODING": "utf-8",
            },
        }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def smoke_test() -> None:
    """Start the server and complete an MCP handshake to prove it launches."""
    code = (
        "import asyncio,sys\n"
        "from mcp import ClientSession, StdioServerParameters\n"
        "from mcp.client.stdio import stdio_client\n"
        "async def m():\n"
        f"  p=StdioServerParameters(command=sys.executable,args=[{str(INSTALL_DIR / 'server.py')!r}])\n"
        "  async with stdio_client(p) as (r,w):\n"
        "    async with ClientSession(r,w) as s:\n"
        "      await s.initialize(); t=await s.list_tools(); print(len(t.tools))\n"
        "asyncio.run(m())\n"
    )
    out = subprocess.run([str(venv_python()), "-c", code], capture_output=True, text=True, timeout=120)
    if out.returncode != 0 or not out.stdout.strip().isdigit():
        print(out.stderr[-2000:], file=sys.stderr)
        sys.exit("Smoke test failed: the server did not start. See the error above.")
    say(f"Server starts OK and offers {out.stdout.strip()} tools")


def main() -> None:
    if sys.version_info < (3, 10):
        sys.exit(f"Python 3.10+ is required (this is {platform.python_version()}).")
    if "--uninstall" in sys.argv:
        path = update_config(remove=True)
        shutil.rmtree(INSTALL_DIR, ignore_errors=True)
        say(f"Removed {SERVER_NAME} from {path} and deleted {INSTALL_DIR}. Restart Claude Desktop.")
        return
    fetch_sources()
    install_python_env()
    smoke_test()
    path = update_config()
    say(f"Added '{SERVER_NAME}' to {path}")
    print("\nDone. Quit Claude Desktop completely and reopen it. In a chat, open the tools menu,")
    print("check that 'crawl4ai' is enabled, and ask e.g. \"Use crawl4ai to summarise https://...\"")


if __name__ == "__main__":
    main()
