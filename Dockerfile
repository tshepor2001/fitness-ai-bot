FROM python:3.12-slim

# Node.js (for TrainingPeaks MCP via npx)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates git && \
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

# Install garmin-mcp system-wide, overriding the pinned garminconnect==0.2.38
# with 0.3.x which uses the newer auth that avoids Garmin SSO 429 rate limits.
RUN uv pip install --system --no-deps "git+https://github.com/Taxuspt/garmin_mcp" && \
    uv pip install --system \
        "garminconnect>=0.3.0" \
        "garth>=0.5.17,<0.6.0" \
        "mcp>=1.23.0" \
        "python-dotenv==1.0.1" \
        "requests==2.32.4"

VOLUME /app/data
EXPOSE 8000

# Default to the HTTP API; override with "python -m fitness_ai_bot.main" for Telegram bot
CMD ["python", "-m", "fitness_ai_bot.http_api"]
