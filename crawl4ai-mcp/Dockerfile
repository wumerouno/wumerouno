# Remote Crawl4AI MCP server for use as a claude.ai custom connector.
# Run:  docker build -t crawl4ai-mcp crawl4ai-mcp/
#       docker run -p 8000:8000 -e MCP_AUTH_TOKEN=<random 32+ chars> --shm-size=1g crawl4ai-mcp
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    CRAWL4_AI_BASE_DIRECTORY=/data \
    MCP_TRANSPORT=http \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && python -m playwright install --with-deps chromium \
 && mkdir -p /data && useradd -m app && chown -R app /data /ms-playwright

COPY server.py .
USER app
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz')"
CMD ["python", "server.py", "--http"]
