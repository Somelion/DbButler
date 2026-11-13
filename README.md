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
- **Workload Map** — a graph of the tables your queries really join, built from their plans and
  weighted by DB time. It shows joins with no foreign key behind them, foreign keys nobody joins on,
  and unindexed foreign keys. It has an optional 3D view, where height is DB time and each schema
  forms its own cluster, and a time slider for looking at last week's workload.
- **Time Machine** — one correlated timeline per database:
  - metric lines;
  - lock incidents, from lock trees recorded every 15 s while anyone is blocked;
  - plan changes;
  - findings opening and resolving;
  - restarts, stats resets and configuration changes.

  Pick any moment to see who blocked whom, which queries ran and what had just changed, or replay
  an incident snapshot by snapshot. An optional AI post-mortem sends facts only, never query text.
- **Table Health** — per-table dead-tuple %, vacuum staleness, last analyze, wraparound age, and
  cache hit %, with one-click Analyze / Vacuum / reset-stats actions.
- **Query Intelligence & Explain** — `pg_stat_statements` browser, paste-a-query plan analysis
  with plain-language red-flag findings, and optional AI-assisted query/index analysis.
- **Advisor** — Index Advisor, Schema Lint, Configuration Advisor, Replication Advisor, Backup &
  WAL Health, Pre-Upgrade Scan, Security & Compliance, Extensions, and **Refactoring**. Each has
  concrete findings and copyable fixes. Refactoring gives schema advice from the workload:
  - hot joins with no foreign key, and join columns with no index;
  - partitioning candidates;
  - write amplification and index consolidation;
  - skewed filter columns, cold columns and wide rows.
- **What-if Indexes** — estimates what a candidate index would do for the tracked queries using
  HypoPG hypothetical indexes, without building anything. It shows the cost change per query, the
  size, and the plans before and after.
- **Plan viewer** — Explain results and What-if plans open in pev2 (Dalibo), and Plan Regressions
  compare two captured plan shapes side by side.
- **Index Testing** — after you build an index for real, check whether it actually helped.
- **Trends** — cache hit rate, connections, worst-table bloat, and database size over 24h/7d/30d,
  plus per-table history for up to 6 selected tables at once.
- **Migration Linter** — paste a migration and see what each statement locks, for how long, and
  what's risky: a non-concurrent index, a table rewrite, validation under lock, a missing
  `lock_timeout`. It uses PostgreSQL's own parser (pglast) and never runs the SQL. Against a
  connected database it also predicts the impact right now: table size, traffic, and the sessions a
  lock would queue behind.
- **Weekly Reports** — every Monday, one page per database: findings opened and resolved, metric
  ranges, the heaviest queries, lock incidents, plan changes, table growth and server events. You
  can print it to PDF, download it as HTML, and optionally post a summary to the alert webhook.
- **MCP server and Prometheus** — read-only integrations, each with its own token (see below).
- **English and Estonian** — an EN / ET switch in the top bar. It starts in the browser's language
  and remembers the choice. Texts the backend writes (findings, the weekly report) are English for
  now.

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
table, duplicate/unused indexes, and a forgotten prepared transaction holding back VACUUM) so
every view has something real to show:

```
docker compose --profile demo up
```

In the Connect screen, use `pgdba-demo-target` as the host and `5432` as the port — that's the
demo container's name on the compose network, not the `5433` port mapped to your host machine
(that mapping is only for reaching it with `psql` or another external tool). Username/password
default to `demo`/`demo`.

No `.env` file or other setup is required for the demo — sensible local-dev defaults are baked
into `docker-compose.yml`. `example.env` documents every override available if you want one.

The forgotten prepared transaction means VACUUM can't remove rows deleted after the demo was
seeded — the Dashboard's Vacuum Horizon tile shows it, with the fix
(`ROLLBACK PREPARED 'demo_forgotten_2pc';`). The seed only runs on an empty volume, so to re-seed
an existing demo database remove just its volume: `docker compose --profile demo rm -sf
pgdba-demo-target`, then `docker volume rm` the volume ending in `_pgdba_demo_data` (see
`docker volume ls`). Don't use `down -v` for this — it also deletes the store's history.

The demo image is built from `demo/Dockerfile`, which is `postgres:16` (Debian) plus the HypoPG
extension that What-if Indexes needs. `docker compose --profile demo up --build` builds it. If your
demo volume was created by the earlier `postgres:16-alpine` image, re-seed it as above. The two
libc's sort text differently, so the old volume's text indexes don't carry over. The Refactoring
Advisor's partitioning rule ignores tables under 1 GB by default. To see it on the demo's
`audit_log`, set `REFACTOR_PARTITION_MIN_TABLE_MB=5` in `.env`.

To give the Time Machine something to replay, `sh demo/stage-lock-incident.sh` (from the repository
root, while the demo is up) stages a 90-second lock incident: three sessions queue behind one row
lock. Within a minute it shows up under Monitor → Time Machine.

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

### Optional: AI agents (MCP) and Prometheus

Both are off until you set their token in `.env`. Each token is separate from `APP_AUTH_TOKEN` and
opens only its own endpoint, never the rest of the API.

- **MCP server** (`APP_MCP_TOKEN`) — lets Claude Code, Cursor or any other MCP client ask "what's
  wrong with this database?" through 12 preset, read-only tools. These cover findings, a live
  diagnosis, top queries, plan regressions, lock incidents, metric history, the Workload Map,
  refactoring suggestions, the migration linter and the weekly report. No tool runs SQL it's given,
  and query text goes out with literal values masked. The endpoint speaks the current stateless MCP
  protocol (2026-07-28) and the older initialize-based one (2025-03-26 to 2025-11-25):

  ```
  claude mcp add --transport http dbbutler http://localhost:5173/api/mcp \
    --header "Authorization: Bearer <APP_MCP_TOKEN>"
  ```

- **Prometheus** (`APP_METRICS_TOKEN`) — collected metrics, open-finding counts, lock waits and
  collector health per database. Scrape `/api/metrics` through the UI, or `/metrics` on the backend
  port, with the token as a bearer credential. Settings → Integrations shows a ready-made scrape
  config.

Set `APP_PUBLIC_URL` to the address people use to open DbButler. The weekly report's webhook
message then links to the report.

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

# ui (Node 22.22+ or 24 — react-router 8 needs it)
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

ESLint's accessibility (`jsx-a11y`) and React hooks rules are errors, so CI fails on a clickable
`div`, an unlabelled input or data fetching that sets state inside an effect.

### UI conventions

- **Styling:**
  - Every component has its own CSS Module (`Foo.module.css`), and every value comes from a
    token in `ui/src/styles/tokens.css`. That file defines the light and dark palettes, type
    scale, spacing, radii, shadows and motion. No raw colours.
  - Inline styles are only for data-driven values, such as a bar width or a chart coordinate.
- **Shared components** are in `ui/src/components/`:
  - `Card`, `SegmentedControl`, `Tabs`, `Badge`/`SeverityBadge`, `Alert`, `EmptyState`,
    `Skeleton`, `Stat`
  - `Dialog`, `useConfirm`, `useToast`, `Popover`, `CopyButton`, `Field`, `SortHeader`
- **Data fetching** goes through TanStack Query. Keys are per database (see
  `ui/src/queryClient.js`), and requests are cancelled when you switch database. Polling pauses
  while the tab is hidden.
- **Routing** uses react-router. Screens live at `/t/<database id>/<screen>` (see
  `ui/src/routes.js`), so reload, back/forward and shared links work. View state worth sharing
  (time range, selected table or query, Advisor tab) is kept in the URL.
- **Icons** are from `lucide-react`, and the fonts are self-hosted through `@fontsource`.

## Documentation

Design docs, the functional spec, and the knowledge base the analysis/rule logic is built from are
kept locally (`docs/`, `databank/`) but aren't part of this repo.
