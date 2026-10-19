# Munkymind API + MCP image.
# Supply chain (v0.2.1): base image pinned by digest, uv pinned, dependencies installed from
# uv.lock with --require-hashes (a tampered or changed package fails the build), and no
# dev/test tools in the image. Update the pins deliberately (Dependabot proposes them).
#
# Least privilege: dependencies are built in a separate stage, so compilers never reach the
# runtime image, and the app runs as an unprivileged user (uid 10001), not root.

# ── Build stage: compilers live here only ────────────────────────────────────
FROM python:3.11-slim@sha256:bab1b7ef4b450c81002278d035eff85ebe394ae94df904f7a3ba14f7e16e487b AS build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.11.5

WORKDIR /app

# Locked, hash-verified runtime dependencies (no dev extras), then the package itself,
# into a self-contained virtualenv that the runtime stage copies.
RUN uv venv /opt/venv
COPY pyproject.toml uv.lock README.md ./
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt -o /tmp/requirements.txt \
    && uv pip install --python /opt/venv/bin/python --no-cache --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt
COPY mm/ ./mm/
RUN uv pip install --python /opt/venv/bin/python --no-cache --no-deps .

# ── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.11-slim@sha256:bab1b7ef4b450c81002278d035eff85ebe394ae94df904f7a3ba14f7e16e487b

# Pick up Debian security fixes released since the base digest was pinned (the image
# scan in CI fails on fixable HIGH/CRITICAL). curl is used by the compose healthcheck.
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# The app runs from /opt/venv; the base image's own pip/setuptools/wheel are never used
# at runtime, so remove them rather than ship (and patch) them.
RUN python -m pip uninstall -y pip setuptools wheel

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /home/app --create-home \
       --shell /usr/sbin/nologin app

COPY --from=build /opt/venv /opt/venv

WORKDIR /app
COPY mm/ ./mm/
COPY scripts/ ./scripts/
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Data directory (mounted as volume in production), owned by the app user.
RUN mkdir -p /data && chown app:app /data

ENV PATH=/opt/venv/bin:$PATH
ENV DATA_ROOT=/data
ENV HOME=/home/app
ENV PYTHONUNBUFFERED=1

USER app

EXPOSE 8000 8001

ENTRYPOINT ["docker-entrypoint.sh"]
