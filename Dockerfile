FROM python:3.12-slim

# Node.js (for TrainingPeaks MCP via npx)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Playwright's Chromium and its OS dependencies
RUN npx playwright install chromium --with-deps

# uv (for Garmin MCP via uvx)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY --from=ghcr.io/astral-sh/uv:latest /uvx /usr/local/bin/uvx

WORKDIR /app
COPY pyproject.toml .
COPY fitness_ai_bot/ fitness_ai_bot/

RUN uv pip install --system .

# Pre-warm: cache the Garmin MCP package so first connect is fast
RUN uvx --python 3.12 --from "git+https://github.com/Taxuspt/garmin_mcp" garmin-mcp --help || true

VOLUME /app/data
EXPOSE 8000

# Default to the HTTP API; override with "python -m fitness_ai_bot.main" for Telegram bot
CMD ["python", "-m", "fitness_ai_bot.http_api"]
