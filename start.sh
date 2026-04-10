#!/bin/sh
# Container entrypoint for izabael.com.
#
# If Litestream is configured (LITESTREAM_BUCKET set), restore the
# SQLite DB from the replica when missing, then run uvicorn under
# `litestream replicate -exec` so writes are continuously streamed
# back to the bucket.
#
# If Litestream is not configured, fall back to plain uvicorn. This
# lets the image deploy safely before the B2 bucket exists.

set -e

DB_PATH="${IZABAEL_DB:-/data/izabael.db}"
UVICORN_CMD="uvicorn app:app --host 0.0.0.0 --port 8000"

mkdir -p "$(dirname "$DB_PATH")"

if [ -n "$LITESTREAM_BUCKET" ]; then
    echo "[start] Litestream enabled — bucket=$LITESTREAM_BUCKET"

    if [ ! -f "$DB_PATH" ]; then
        echo "[start] $DB_PATH missing — attempting restore from replica"
        litestream restore -if-replica-exists -o "$DB_PATH" \
            -config /etc/litestream.yml "$DB_PATH" || \
            echo "[start] No replica to restore (first boot is fine)"
    fi

    exec litestream replicate -config /etc/litestream.yml -exec "$UVICORN_CMD"
else
    echo "[start] Litestream not configured — running uvicorn directly"
    exec $UVICORN_CMD
fi
