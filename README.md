# DbButler

An automated PostgreSQL DBA assistant — connects read-only to a PostgreSQL database, surfaces
health findings and misconfiguration/anti-pattern warnings, and generates hardware-aware config
tuning suggestions. Built for both experienced DBAs and non-expert home users.

## What it does

DbButler runs as a self-hosted Docker stack alongside the database(s) you want to watch. It
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
`APP_AUTH_TOKEN`, since the UI won't get past its token prompt without it). It also refuses an
`APP_AUTH_TOKEN` that's a well-known value or shorter than 32 characters.

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

### Connecting with a least-privilege role

Connect DbButler to each database with a dedicated monitoring role, not a superuser or the
application's owner role. Every check only needs read access to statistics and catalogs:

```sql
CREATE ROLE dbbutler LOGIN PASSWORD '<a long random password>';
GRANT pg_monitor TO dbbutler;          -- pg_stat_*, pg_ls_waldir(), pg_stat_statements, settings
GRANT CONNECT ON DATABASE <db> TO dbbutler;
```

DbButler adds its own guard rails on top — EXPLAIN ANALYZE runs in a read-only, always-rolled-back
transaction; every session carries a statement timeout, a lock timeout and
`application_name = dbbutler` — but the role is what actually limits what the app *can* do. The
explicit write actions (Vacuum/Analyze, Apply, cancel/terminate, reset stats) need more: table
ownership or, on PostgreSQL 17+, `pg_maintain`, plus `pg_signal_backend` for cancel/terminate.
Grant those only if you want to use those buttons.

If the target is reached through PgBouncer, the pooler may reject the `options` startup parameter
these timeouts travel in; DbButler then retries without it, so it still connects, but those
server-side timeouts don't apply on that target. Connecting DbButler straight to PostgreSQL (not
through the pooler) keeps them.

### Running behind a reverse proxy / on a custom hostname

The `pgdba-ui` container is nginx serving the built UI, and it reverse-proxies every `/api/*`
request straight to `pgdba-backend` over the Docker network (see `ui/nginx.conf`) — the browser
only ever talks to whatever single origin fronts `pgdba-ui`. That means it works unmodified behind
a reverse proxy on any hostname: no `VITE_API_BASE`, no CORS setup, and no host-header allowlist to
maintain. If you're hitting the backend's `BACKEND_PORT` directly from something other than this
UI (a script, a different frontend), `CORS_ALLOWED_ORIGINS` in `example.env` still applies to that
path.

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

### Optional: TimescaleDB test target

To test TimescaleDB support, layer the extra compose file on top. It starts a TimescaleDB container
and seeds it on first start with random sensor/event hypertables (compressed chunks, a continuous
aggregate, an over-chunked hypertable) plus a few deliberate schema issues:

```
docker compose -f docker-compose.yml -f docker-compose.timescaledb.yml up
```

In the Connect screen use host `pgdba-timescale-target`, port `5432`, user/password/database
`tsdemo` (host port `5434` is only for external tools). To re-seed from scratch, run the same
command with `down -v`.

### Optional: logging to files

Set `LOG_TO_FILE=true` in `.env` to make each container also write its own log file into `./logs`
(change with `LOG_HOST_DIR`): `backend.log`, `ui-access.log`, `ui-error.log`, `store.log`,
`demo-target.log`, `timescale-target.log`. Postgres containers log only to the file while this is
on. See `example.env`.

The running version (from `.version`) is served at `/version` (and `/api/version` through the UI).

## Development

CI (`.github/workflows/ci.yml`) runs on every push to `main` and every pull request: ruff,
pytest and pip-audit for the backend; ESLint, a production build and npm audit for the UI; and a
build of both Docker images (checking the backend image runs as a non-root user).

To run the same checks locally:

```
# backend (Python 3.12)
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install --require-hashes -r requirements.txt -r requirements-dev.txt   # bin/ on macOS/Linux
.venv/Scripts/ruff check .
.venv/Scripts/python -m pytest

# ui (Node 20.19+, 22.13+ or 24)
cd ui
npm ci
npm run lint
npm run build
```

The tests need no database: they cover the pure analysis/guard logic and the HTTP layer through
FastAPI's TestClient without starting the app's lifespan.

Dependencies are locked: `backend/requirements.txt` / `requirements-dev.txt` are generated with
hashes from `requirements.in` / `requirements-dev.in` (the command is at the top of each `.in`
file — `--universal` keeps Linux-only transitive deps in the lock even when it's generated on
Windows or macOS), and the UI uses `package-lock.json` with `npm ci`. To upgrade, re-run the
compile command with `--upgrade` (or `npm update` in `ui/`), then run the checks above.

ESLint's accessibility (`jsx-a11y`) and `react-hooks/set-state-in-effect` findings are warnings
for now, not errors — see the comment in `ui/eslint.config.js`.

## Documentation

Design docs, the functional spec, and the knowledge base the analysis/rule logic is built from are
kept locally (`docs/`, `databank/`) but aren't part of this repo.
