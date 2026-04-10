FROM python:3.12-slim

# Litestream — continuous SQLite replication to a B2 bucket.
# Pinned to a known-good 0.3.x release. Activated at runtime if
# LITESTREAM_BUCKET is set; otherwise start.sh runs uvicorn directly.
ARG LITESTREAM_VERSION=v0.3.13
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates wget && \
    wget -qO /tmp/litestream.tar.gz \
        "https://github.com/benbjohnson/litestream/releases/download/${LITESTREAM_VERSION}/litestream-${LITESTREAM_VERSION}-linux-amd64.tar.gz" && \
    tar -xzf /tmp/litestream.tar.gz -C /usr/local/bin litestream && \
    chmod +x /usr/local/bin/litestream && \
    rm /tmp/litestream.tar.gz && \
    apt-get purge -y wget && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY litestream.yml /etc/litestream.yml
RUN chmod +x /app/start.sh && mkdir -p /data data
EXPOSE 8000
CMD ["/app/start.sh"]
