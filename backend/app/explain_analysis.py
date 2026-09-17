"""Pattern-matches EXPLAIN (ANALYZE, BUFFERS) plan text for the specific red
flags the databank's own worked example is built around, and translates each
into a plain-language finding. See:
databank/05 - Examples/Example - Analyzing a Slow Query with EXPLAIN ANALYZE.md
"""

import re

SEQ_SCAN_PATTERN = re.compile(r"Seq Scan on (\S+)")
ROWS_REMOVED_PATTERN = re.compile(r"Rows Removed by Filter:\s*(\d+)")
DISK_SORT_PATTERN = re.compile(r"Sort Method:\s*external merge\s*Disk", re.IGNORECASE)
BUFFERS_READ_PATTERN = re.compile(r"Buffers:.*?\bread=(\d+)")
EXECUTION_TIME_PATTERN = re.compile(r"Execution Time:\s*([\d.]+)\s*ms")
FUNCTION_WRAP_PATTERN = re.compile(
    r"Filter:.*?\b(DATE|LOWER|UPPER|EXTRACT|TRUNC|CAST)\(([a-zA-Z_][a-zA-Z0-9_]*)\)", re.IGNORECASE
)

ROWS_REMOVED_ATTENTION = 1_000
ROWS_REMOVED_CRITICAL = 100_000
BUFFERS_READ_ATTENTION = 1_000


def extract_execution_time_ms(plan_text: str) -> float | None:
    match = EXECUTION_TIME_PATTERN.search(plan_text)
    return float(match.group(1)) if match else None


def analyze_plan(plan_text: str) -> list[dict]:
    findings = []

    for table in sorted(set(SEQ_SCAN_PATTERN.findall(plan_text))):
        findings.append(
            {
                "id": f"seq-scan-{table}",
                "category": "query plan",
                "severity": "attention",
                "title": f"Sequential scan on {table}",
                "summary": (
                    f"This query read every row of {table} instead of using an index. "
                    "That's fine for a small table, but worth checking if it's large."
                ),
                "detail": f"Seq Scan on {table}",
                "suggested_action": (
                    "If this table is large and the query is selective, consider adding an "
                    "index that matches the WHERE clause."
                ),
            }
        )

    rows_removed = [int(n) for n in ROWS_REMOVED_PATTERN.findall(plan_text)]
    worst_removed = max(rows_removed, default=0)
    if worst_removed >= ROWS_REMOVED_ATTENTION:
        severity = "critical" if worst_removed >= ROWS_REMOVED_CRITICAL else "attention"
        findings.append(
            {
                "id": "rows-removed-by-filter",
                "category": "query plan",
                "severity": severity,
                "title": "Most of the rows read were thrown away",
                "summary": (
                    f"{worst_removed:,} rows were read and then filtered out. The filter isn't being "
                    "applied by an index, so PostgreSQL had to check each row by hand."
                ),
                "detail": f"Rows Removed by Filter: {worst_removed}",
                "suggested_action": "Add or adjust an index so the filter can be applied during the scan, not after.",
            }
        )

    if DISK_SORT_PATTERN.search(plan_text):
        findings.append(
            {
                "id": "disk-sort",
                "category": "query plan",
                "severity": "critical",
                "title": "A sort spilled to disk",
                "summary": (
                    "This query's sort step ran out of memory (work_mem) and had to use disk, "
                    "which is much slower than sorting in memory."
                ),
                "detail": "Sort Method: external merge Disk",
                "suggested_action": "Raise work_mem for this session, or add an index that avoids the sort entirely.",
            }
        )

    func_match = FUNCTION_WRAP_PATTERN.search(plan_text)
    if func_match:
        func_name, column = func_match.group(1), func_match.group(2)
        findings.append(
            {
                "id": "function-wrapped-column",
                "category": "query plan",
                "severity": "attention",
                "title": f"{func_name}({column}) can't use a normal index",
                "summary": (
                    f"The filter wraps {column} in {func_name}(...), so PostgreSQL can't use a plain "
                    f"index on {column} — it has to check every row."
                ),
                "detail": func_match.group(0),
                "suggested_action": (
                    f"Rewrite the condition to compare {column} directly (e.g. a range instead of "
                    f"{func_name}({column})), or add an expression index."
                ),
            }
        )

    read_pages = [int(n) for n in BUFFERS_READ_PATTERN.findall(plan_text)]
    worst_read = max(read_pages, default=0)
    if worst_read >= BUFFERS_READ_ATTENTION:
        findings.append(
            {
                "id": "heavy-disk-io",
                "category": "query plan",
                "severity": "attention",
                "title": "This query pulled a lot of data from disk",
                "summary": f"{worst_read:,} pages were read from disk rather than served from cache.",
                "detail": f"Buffers: read={worst_read}",
                "suggested_action": (
                    "This often clears up on its own as the cache warms; if it persists, the working "
                    "set may not fit in memory."
                ),
            }
        )

    return findings
