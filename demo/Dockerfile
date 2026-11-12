# Demo target: PostgreSQL 16 plus HypoPG, so the What-if Indexes screen can
# cost hypothetical indexes against the demo workload. The Debian-based
# official image keeps the apt.postgresql.org repository configured, where
# HypoPG is packaged per PostgreSQL major version.
FROM postgres:16-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends "postgresql-${PG_MAJOR}-hypopg" \
    && rm -rf /var/lib/apt/lists/*
