"""Hardware-aware postgresql.conf recommendations.

Calibrated against the worked 16GB RAM / SSD example in
databank/05 - Examples/Example - Configuring postgresql.conf.md (shared_buffers
25% of RAM, effective_cache_size 75%, wal/checkpoint defaults, random_page_cost
1.1 on SSD, autovacuum tuned tighter than stock defaults) and the work_mem
safety inequality warned about in databank/02 - Concepts/PostgreSQL Bad
Practices.md ("100 connections x 10 nodes x 1GB work_mem = 1TB").

One function per parameter would be the ideal shape for "add a check without
touching the others" the way collectors/rules are structured elsewhere in
this app, but most of these values share the same few inputs (RAM, workload)
closely enough that a single `recommend()` reads more clearly than fifteen
one-line functions — reconsider splitting if a parameter's logic grows
independent enough to need its own tests.
"""

RESTART_REQUIRED = {"shared_buffers", "max_connections", "wal_buffers"}

SORT_NODES_BY_WORKLOAD = {"oltp": 2, "mixed": 3, "analytics": 6, "home": 2}
VACUUM_SCALE_BY_WORKLOAD = {"oltp": 0.02, "mixed": 0.05, "analytics": 0.1, "home": 0.1}
ANALYZE_SCALE_BY_WORKLOAD = {"oltp": 0.01, "mixed": 0.02, "analytics": 0.05, "home": 0.05}

# Analytics workloads legitimately sit idle-in-transaction longer between big
# queries (e.g. a BI tool building a multi-step report), so its timeout is
# looser than the other three workloads.
IDLE_IN_TX_TIMEOUT_BY_WORKLOAD = {"oltp": "30min", "mixed": "30min", "analytics": "60min", "home": "30min"}

# statement_timeout here is a safety net against a runaway query, not a hard
# SLA — so it's set loose enough to not interrupt legitimate long-running
# work for each workload's normal shape.
STATEMENT_TIMEOUT_BY_WORKLOAD = {"oltp": "30s", "mixed": "5min", "analytics": "30min", "home": "5min"}


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _format_mb(value_mb: int) -> str:
    if value_mb >= 1024 and value_mb % 1024 == 0:
        return f"{value_mb // 1024}GB"
    if value_mb >= 1024:
        return f"{value_mb / 1024:.1f}GB"
    return f"{value_mb}MB"


def _max_connections(ram_mb: int) -> int:
    if ram_mb < 2048:
        return 20
    if ram_mb < 4096:
        return 40
    if ram_mb < 8192:
        return 60
    if ram_mb < 16384:
        return 80
    return 100


def recommend(ram_mb: int, cpu_cores: int, storage_type: str, workload_type: str) -> list[dict]:
    is_ssd = storage_type == "ssd"

    shared_buffers_mb = _clamp(int(ram_mb * 0.25), 128, 16384)
    effective_cache_size_mb = int(ram_mb * 0.75)
    maintenance_work_mem_mb = _clamp(ram_mb // 32, 64, 2048)
    max_connections = _max_connections(ram_mb)

    sort_nodes = SORT_NODES_BY_WORKLOAD.get(workload_type, 3)
    # Same 25%-of-RAM budget as shared_buffers, split across every connection's
    # worst-case concurrent sort/hash nodes — the shape of the safety
    # inequality above, not just its warning.
    work_mem_mb = _clamp(int((ram_mb * 0.25) / (max_connections * sort_nodes)), 4, 256)

    autovacuum_workers = _clamp(cpu_cores // 2, 3, 8)
    vacuum_scale = VACUUM_SCALE_BY_WORKLOAD.get(workload_type, 0.05)
    analyze_scale = ANALYZE_SCALE_BY_WORKLOAD.get(workload_type, 0.02)
    vacuum_cost_delay_ms = 2 if is_ssd else 10

    idle_in_tx_timeout = IDLE_IN_TX_TIMEOUT_BY_WORKLOAD.get(workload_type, "30min")
    statement_timeout = STATEMENT_TIMEOUT_BY_WORKLOAD.get(workload_type, "5min")

    if is_ssd:
        random_page_cost, random_page_cost_why = (
            "1.1",
            "The default (4.0) assumes spinning disks; SSD random reads are nearly as fast as sequential.",
        )
        io_concurrency, io_concurrency_why = "200", "SSDs handle many concurrent reads well (default is 1)."
    else:
        random_page_cost, random_page_cost_why = "4.0", "The default already matches spinning-disk behavior."
        io_concurrency, io_concurrency_why = "1", "Spinning disks don't benefit from concurrent read requests."

    return [
        {
            "name": "shared_buffers",
            "recommended_display": _format_mb(shared_buffers_mb),
            "why": "25% of RAM — how much data PostgreSQL keeps in its own cache.",
        },
        {
            "name": "effective_cache_size",
            "recommended_display": _format_mb(effective_cache_size_mb),
            "why": "~75% of RAM — a planner hint for how much the OS can cache, not an allocation.",
        },
        {
            "name": "work_mem",
            "recommended_display": _format_mb(work_mem_mb),
            "why": f"Sized against {max_connections} connections so concurrent sorts stay in memory, not disk.",
        },
        {
            "name": "maintenance_work_mem",
            "recommended_display": _format_mb(maintenance_work_mem_mb),
            "why": "Speeds up VACUUM and index builds — safe to size generously since few run at once.",
        },
        {
            "name": "max_connections",
            "recommended_display": str(max_connections),
            "why": "Scaled to available RAM — use a pooler (PgBouncer) rather than raising this further.",
        },
        {
            "name": "wal_buffers",
            "recommended_display": "16MB",
            "why": "A flat 16MB is enough regardless of hardware size.",
        },
        {
            "name": "checkpoint_completion_target",
            "recommended_display": "0.9",
            "why": "Spreads checkpoint I/O out instead of bursting it.",
        },
        {
            "name": "max_wal_size",
            "recommended_display": "2GB",
            "why": "Gives WAL room to breathe between checkpoints on a write-active database.",
        },
        {
            "name": "min_wal_size",
            "recommended_display": "256MB",
            "why": "Avoids needlessly recycling WAL segments on a quiet database.",
        },
        {
            "name": "checkpoint_timeout",
            "recommended_display": "10min",
            "why": "Matches max_wal_size for a steady checkpoint cadence.",
        },
        {
            "name": "random_page_cost",
            "recommended_display": random_page_cost,
            "why": random_page_cost_why,
        },
        {
            "name": "effective_io_concurrency",
            "recommended_display": io_concurrency,
            "why": io_concurrency_why,
        },
        {
            "name": "autovacuum_max_workers",
            "recommended_display": str(autovacuum_workers),
            "why": "Scaled to CPU cores — more workers means bloat gets cleaned up faster on busy tables.",
        },
        {
            "name": "autovacuum_vacuum_scale_factor",
            "recommended_display": str(vacuum_scale),
            "why": f"Default (0.2) waits for 20% dead rows; {workload_type} workloads do better triggering sooner.",
        },
        {
            "name": "autovacuum_analyze_scale_factor",
            "recommended_display": str(analyze_scale),
            "why": "Keeps the planner's statistics fresher than the 0.1 default.",
        },
        {
            "name": "autovacuum_vacuum_cost_delay",
            "recommended_display": f"{vacuum_cost_delay_ms}ms",
            "why": "How much autovacuum throttles itself — lower on faster storage.",
        },
        {
            "name": "idle_in_transaction_session_timeout",
            "recommended_display": idle_in_tx_timeout,
            "why": f"Automatically ends a session left idle inside a transaction — sized for {workload_type} workloads.",
        },
        {
            "name": "statement_timeout",
            "recommended_display": statement_timeout,
            "why": f"A safety net against a runaway query, not a hard limit — sized loose for {workload_type} workloads.",
        },
    ]
