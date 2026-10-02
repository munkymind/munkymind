# Munkymind API + MCP image.
# Supply chain (v0.2.1): base image pinned by digest, uv pinned, dependencies installed from
# uv.lock with --require-hashes (a tampered or changed package fails the build), and no
# dev/test tools in the image. Update the pins deliberately (Dependabot proposes them).
FROM python:3.11-slim@sha256:bab1b7ef4b450c81002278d035eff85ebe394ae94df904f7a3ba14f7e16e487b

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.11.5

# Locked, hash-verified runtime dependencies (no dev extras), then the package itself.
COPY pyproject.toml uv.lock README.md ./
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt -o /tmp/requirements.txt \
    && uv pip install --system --no-cache --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt
COPY mm/ ./mm/
COPY scripts/ ./scripts/
RUN uv pip install --system --no-cache --no-deps .

# Data directory (mounted as volume in production)
RUN mkdir -p /data

ENV DATA_ROOT=/data
ENV PYTHONUNBUFFERED=1

EXPOSE 8000 8001
