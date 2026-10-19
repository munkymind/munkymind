#!/bin/sh
# Munkymind container entrypoint.
#
# The image runs as the unprivileged "app" user (uid 10001). Data volumes created by
# earlier releases (which ran as root) are owned by root, and some hosts (e.g. Railway)
# always mount volumes as root. To handle both:
#
#   - Started as root (compose's volume-init service, `docker run --user 0`, or
#     RAILWAY_RUN_UID=0): hand the data directory to the app user, then drop to it with no
#     capabilities before running the command.
#   - Started as the app user (the default): check the data directory is writable and,
#     if not, explain how to fix it instead of failing later with a stack trace.
set -eu

APP_UID=10001
APP_GID=10001
DATA_DIR="${DATA_ROOT:-/data}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    find "$DATA_DIR" \( ! -user "$APP_UID" -o ! -group "$APP_GID" \) \
        -exec chown -h "$APP_UID:$APP_GID" {} +
    exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --clear-groups \
        --inh-caps=-all -- "$@"
fi

if [ -d "$DATA_DIR" ] && [ ! -w "$DATA_DIR" ]; then
    echo "munkymind: $DATA_DIR is not writable by uid $(id -u)." >&2
    echo "  It was probably created by an older (root) release, or your host mounts volumes as root." >&2
    echo "  Fix it once by starting the container as root; it repairs ownership, then drops to a non-root user:" >&2
    echo "    docker compose:  docker compose up -d   (the volume-init service does this)" >&2
    echo "    docker run:      add --user 0" >&2
    echo "    Railway:         set the service variable RAILWAY_RUN_UID=0" >&2
    exit 1
fi

exec "$@"
