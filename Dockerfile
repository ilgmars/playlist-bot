# syntax=docker.io/docker/dockerfile:1.4

FROM python:3.12-slim-bookworm AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
EXPOSE 8000

FROM base AS deps
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      ca-certificates \
      curl \
      ffmpeg \
      && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir hatchling hatch-vcs 2>/dev/null; \
    pip install --no-cache-dir .

FROM deps AS test
RUN pip install --no-cache-dir ".[dev]" "bandit[toml]"
COPY tests ./tests

FROM base AS runtime
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY src/playlistbot ./src/playlistbot
ENTRYPOINT ["python", "-m", "playlistbot"]
