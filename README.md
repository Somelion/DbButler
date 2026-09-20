# DbButler

An automated PostgreSQL DBA assistant — connects read-only to a PostgreSQL database, surfaces
health findings and misconfiguration/anti-pattern warnings, and generates hardware-aware config
tuning suggestions. Built for both experienced DBAs and non-expert home users.

## What it does

PostgreDba runs as a self-hosted Docker stack alongside the database(s) you want to watch. It
never installs an agent on the target host — every check is a read-only SQL query, and the only
writes it makes are explicit, per-item actions you trigger yourself (e.g. Analyze, Vacuum, reset
stats, cancel/terminate a session). Config recommendations are always advisory and copy/paste —
nothing is applied automatically. An optional seeded demo database is included so you can try
every screen without connecting anything real.

## Key features

- **Dashboard** — traffic-light health overview (connections, locks, bloat, cache hit rate,
  checkpoints, wraparound, autovacuum, replication, backup/WAL, pooler) with a live findings feed.
- **Diagnose Now** — one button that runs the full health runbook and surfaces the top problem
  first.
- **Activity** — live session view with blocking-lock detection and cancel/terminate.
- **Table Health** — per-table dead-tuple %, vacuum staleness, last analyze, wraparound age, and
  cache hit %, with one-click Analyze / Vacuum / reset-stats actions.
- **Query Intelligence & Explain** — `pg_stat_statements` browser, paste-a-query plan analysis
  with plain-language red-flag findings, and optional AI-assisted query/index analysis.
- **Advisor** — Index Advisor, Schema Lint, Configuration Advisor, Replication Advisor, Backup &
  WAL Health, Pre-Upgrade Scan, Security & Compliance, and Extensions, each with concrete findings
  and copyable fixes.
- **Index Testing** — try a candidate index's effect before committing to it.
- **Trends** — cache hit rate, connections, worst-table bloat, and database size over 24h/7d/30d,
  plus per-table history for up to 6 selected tables at once.

## Tech stack

React (Vite) frontend, FastAPI (Python) backend, its own Postgres "store" database for
history/metrics, all wired together with Docker Compose.

## Prerequisites

Docker and Docker Compose. Nothing else needs to be installed locally to run the app.

## Getting started

**Required first step**: generate two secrets and put them in `.env` — the backend refuses to
start without both (a real `APP_SECRET_KEY`, since it encrypts every stored credential; a real
`APP_AUTH_TOKEN`, since the UI won't get past its token prompt without it).

```
cp example.env .env
openssl rand -hex 32          # run twice — paste one output as APP_SECRET_KEY=, the other as APP_AUTH_TOKEN=
```

Then, optional: run `bash pre-flight.sh` — it checks for a `.env` file (including whether both of
the above are actually set to real values) and whether the ports below are already taken,
suggesting a free one if so. It only reports; it never starts anything itself.

```
docker compose up
```

- UI: http://localhost:5173
- Backend API: http://localhost:8000 (`/health`, `/health/store`)

Any of the three host ports can be overridden (e.g. if one is already taken) by exporting
`UI_PORT`, `BACKEND_PORT`, or `DEMO_PORT` before running `docker compose up`, or by editing them in
`.env` — `pre-flight.sh` tells you which to use.

### Optional: demo database

To also start a disposable demo Postgres instance, seeded with a small dataset that has a few
deliberate issues (an unindexed foreign key, float-typed money columns, an unvacuumed bloated
table, duplicate/unused indexes) so every view has something real to show:

```
docker compose --profile demo up
```

In the Connect screen, use `pgdba-demo-target` as the host and `5432` as the port — that's the
demo container's name on the compose network, not the `5433` port mapped to your host machine
(that mapping is only for reaching it with `psql` or another external tool). Username/password
default to `demo`/`demo`.

No `.env` file or other setup is required for the demo — sensible local-dev defaults are baked
into `docker-compose.yml`. `example.env` documents every override available if you want one.

## Documentation

Design docs, the functional spec, and the knowledge base the analysis/rule logic is built from are
kept locally (`docs/`, `databank/`) but aren't part of this repo.
