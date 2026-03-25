FROM python:3.12-slim AS base
WORKDIR /app

# Install system dependencies for asyncpg (requires libpq)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc git curl \
    && rm -rf /var/lib/apt/lists/*

# Copy source and install
COPY . .
RUN pip install --no-cache-dir .

# Run as non-root user (L-2: container security)
RUN useradd --system --no-create-home appuser
USER appuser

# --- Playwright stage: adds Chromium browser for web scraper ---
FROM base AS playwright
USER root
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers
RUN pip install playwright && playwright install --with-deps chromium && \
    chmod -R o+rx /opt/playwright-browsers
USER appuser

# --- API stage: no Playwright needed (~1.5GB smaller) ---
FROM base AS api
CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

# --- Fast worker: needs Playwright/Chromium for scraper adapter ---
FROM playwright AS fast-worker
CMD ["arq", "src.workers.settings.WorkerSettings"]

# --- Slow worker: LLM-only, no Playwright needed ---
FROM base AS slow-worker
CMD ["arq", "src.workers.settings.SlowWorkerSettings"]
