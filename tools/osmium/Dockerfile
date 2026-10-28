# syntax=docker/dockerfile:1
# Tools for the data jobs: osmium (merge regions), osm2pgsql + psql (address search import).
FROM debian:bookworm-slim
# Retries and timeouts for slow or unstable connections
RUN printf 'Acquire::Retries "10";\nAcquire::http::Timeout "120";\nAcquire::https::Timeout "120";\n' \
      > /etc/apt/apt.conf.d/80-retries \
 && apt-get update \
 && apt-get install -y --no-install-recommends osmium-tool osm2pgsql postgresql-client \
 && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["/bin/sh"]
