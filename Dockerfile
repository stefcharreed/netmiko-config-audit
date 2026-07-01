FROM python:3.12-slim

WORKDIR /app

# Install pinned deps first so this layer caches across code-only changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# --no-deps: requirements.txt already installed everything; this just registers
# the config-audit / config-audit-mcp entry points from pyproject.toml.
RUN pip install --no-cache-dir -e . --no-deps

ENTRYPOINT ["config-audit"]
