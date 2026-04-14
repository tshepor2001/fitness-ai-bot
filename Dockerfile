FROM python:3.12-slim

# Node.js (for TrainingPeaks MCP via npx)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# uv (for Garmin MCP via uvx)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml .
COPY fitness_ai_bot/ fitness_ai_bot/

RUN uv pip install --system .

VOLUME /app/data

CMD ["python", "-m", "fitness_ai_bot.main"]
