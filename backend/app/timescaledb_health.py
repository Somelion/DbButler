"""TimescaleDB-aware rollup for Table Health's per-table query.

TimescaleDB hypertables store their actual data in per-chunk child tables
under the _timescaledb_internal schema — each chunk is an ordinary table
with its own pg_stat_user_tables row, so table_health.py's TABLE_HEALTH_QUERY
(schema-unaware by design, like every other per-table listing in this app)
shows N cryptically-named chunk rows per hypertable instead of one
meaningful row. Worse, the hypertable's own native pg_class relation is
*also* a row in that same result set, but it's essentially always empty —
TimescaleDB routes every insert to a chunk, never the hypertable itself — so
without this rollup a hypertable's live/dead tuple counts, vacuum
timestamps, and size all read as near-zero/never-touched, which is exactly
the "wrong information" this module fixes.

Only ever called once the caller has confirmed 'timescaledb' is in
pg_extension (same gating pattern as extension_advisor.py) —
timescaledb_information.chunks doesn't exist otherwise.
"""

TIMESCALEDB_INSTALLED_QUERY = "SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'"

# chunk_schema is always '_timescaledb_internal' in current TimescaleDB
# versions, but joined by name here rather than assumed, in case that ever
# changes.
CHUNK_MAP_QUERY = """
    SELECT hypertable_schema, hypertable_name, chunk_schema, chunk_name
    FROM timescaledb_information.chunks
"""

# Positional indices into one TABLE_HEALTH_QUERY row
# (table_health.py::_TABLE_HEALTH_COLUMNS) — this module aggregates/replaces
# specific fields and passes the rest through untouched, so named indices
# are clearer here than unpacking the whole tuple.
(
    _SCHEMA,
    _TABLE,
    _LIVE,
    _DEAD,
    _LAST_VACUUM,
    _LAST_AUTOVACUUM,
    _LAST_ANALYZE,
    _LAST_AUTOANALYZE,
    _XID_AGE,
    _TOTAL_BYTES,
    _CACHE_HIT_PCT,
    _TABLE_OID,
    _RELTOASTRELID,
    _N_TUP_UPD,
    _N_TUP_HOT_UPD,
) = range(15)


def is_timescaledb_installed(cur) -> bool:
    cur.execute(TIMESCALEDB_INSTALLED_QUERY)
    return cur.fetchone() is not None


def _fetch_chunk_map(cur) -> dict[tuple[str, str], tuple[str, str]]:
    cur.execute(CHUNK_MAP_QUERY)
    return {
        (chunk_schema, chunk_name): (hypertable_schema, hypertable_name)
        for hypertable_schema, hypertable_name, chunk_schema, chunk_name in cur.fetchall()
    }


def _most_recent(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return a if a > b else b


def aggregate_hypertable_rows(rows: list[tuple], cur) -> list[tuple]:
    """Rolls chunk rows (and the hypertable's own near-empty native row, if
    present in this result set) up onto one synthetic row per hypertable.
    Ordinary, non-hypertable tables pass through unchanged. Returns a new
    list of rows in the exact same 15-column shape TABLE_HEALTH_QUERY
    produces, safe to hand to _build_table_health_row/_analyze_table as-is."""
    chunk_map = _fetch_chunk_map(cur)
    if not chunk_map:
        return rows
    hypertable_identifiers = set(chunk_map.values())

    passthrough: list[tuple] = []
    # hypertable (schema, name) -> every row contributing to it (its own
    # native row, if this result set has one, plus all of its chunk rows)
    hypertable_rows: dict[tuple[str, str], list[tuple]] = {}

    for row in rows:
        key = (row[_SCHEMA], row[_TABLE])
        hypertable_key = chunk_map.get(key)
        if hypertable_key is not None:
            hypertable_rows.setdefault(hypertable_key, []).append(row)
        elif key in hypertable_identifiers:
            hypertable_rows.setdefault(key, []).append(row)
        else:
            passthrough.append(row)

    aggregated: list[tuple] = []
    for (schema, table), contributing in hypertable_rows.items():
        live = sum(r[_LIVE] or 0 for r in contributing)
        dead = sum(r[_DEAD] or 0 for r in contributing)
        total_bytes = sum(r[_TOTAL_BYTES] or 0 for r in contributing)
        n_tup_upd = sum(r[_N_TUP_UPD] or 0 for r in contributing)
        n_tup_hot_upd = sum(r[_N_TUP_HOT_UPD] or 0 for r in contributing)

        last_vacuum = last_autovacuum = last_analyze = last_autoanalyze = None
        xid_age = None
        hit_pct_num = 0.0
        hit_pct_den = 0
        for r in contributing:
            last_vacuum = _most_recent(last_vacuum, r[_LAST_VACUUM])
            last_autovacuum = _most_recent(last_autovacuum, r[_LAST_AUTOVACUUM])
            last_analyze = _most_recent(last_analyze, r[_LAST_ANALYZE])
            last_autoanalyze = _most_recent(last_autoanalyze, r[_LAST_AUTOANALYZE])
            row_xid_age = r[_XID_AGE]
            if row_xid_age is not None:
                xid_age = row_xid_age if xid_age is None else max(xid_age, row_xid_age)
            weight = (r[_LIVE] or 0) + (r[_DEAD] or 0)
            if r[_CACHE_HIT_PCT] is not None and weight > 0:
                hit_pct_num += r[_CACHE_HIT_PCT] * weight
                hit_pct_den += weight
        cache_hit_pct = (hit_pct_num / hit_pct_den) if hit_pct_den > 0 else None

        # The hypertable's own native relation (if this result set had a row
        # for it) is the one real, ALTER/VACUUM-able relation identity for
        # this hypertable — Postgres accepts VACUUM/ANALYZE on a hypertable
        # name directly and TimescaleDB propagates it to every chunk, so
        # per-table maintenance actions keep working unchanged. Falls back
        # to the first chunk's oid only in the unexpected case the
        # hypertable's own row wasn't present in this result set at all.
        identity_row = next((r for r in contributing if (r[_SCHEMA], r[_TABLE]) == (schema, table)), contributing[0])

        aggregated.append(
            (
                schema,
                table,
                live,
                dead,
                last_vacuum,
                last_autovacuum,
                last_analyze,
                last_autoanalyze,
                xid_age,
                total_bytes,
                cache_hit_pct,
                identity_row[_TABLE_OID],
                identity_row[_RELTOASTRELID],
                n_tup_upd,
                n_tup_hot_upd,
            )
        )

    return passthrough + aggregated
