FROM python:3.14-slim AS base

# gitstore.py shells out to `git` for every commit — not in the base image by default.
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install pinned deps first so this layer caches across code-only changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# --no-deps: requirements.txt already installed everything; this just registers
# the config-audit / config-audit-mcp entry points from pyproject.toml.
RUN pip install --no-cache-dir -e . --no-deps

# `docker build --target test .` runs the real suite inside the image — the
# same environment the runtime stage below ships. Build fails if tests fail.
FROM base AS test
RUN pip install --no-cache-dir "pytest>=8.0"
RUN pytest tests/ -q

# Default target (last stage): what `docker build -t <name> .` produces.
FROM base AS runtime
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser
ENTRYPOINT ["config-audit"]
