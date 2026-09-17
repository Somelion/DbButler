import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { formatMs } from "./format";
import HeaderHint from "./HeaderHint";

const POLL_INTERVAL_MS = 15000;
const COLUMNS = [
  {
    key: "query",
    label: "Query",
    help: "Normalized query text — literal values are replaced with $1, $2, ... by pg_stat_statements.",
  },
  { key: "calls", label: "Calls", help: "How many times this query has run since stats were last reset." },
  {
    key: "total_exec_ms",
    label: "Total",
    help: "Total time spent executing this query across all its calls combined — the best single number for \"which query costs the most.\"",
  },
  { key: "mean_exec_ms", label: "Mean", help: "Average execution time per call." },
  { key: "rows", label: "Rows", help: "Total rows returned or affected across all calls combined." },
  { key: "", label: "" },
];

export default function QueryIntelligence({ targetId, onExplain, onAnalyzeWithAi }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [resetting, setResetting] = useState(false);
  const [sortKey, setSortKey] = useState("total_exec_ms");
  const [sortDir, setSortDir] = useState("desc");

  const refresh = () =>
    api
      .getQueryStats(targetId)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((err) => setError(err.message));

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetId]);

  const handleReset = async () => {
    if (!window.confirm("Reset all query statistics? The top-queries list will start empty again.")) return;
    setResetting(true);
    setError(null);
    try {
      await api.resetQueryStats(targetId);
      await refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setResetting(false);
    }
  };

  const handleSort = (key) => {
    if (!key) return;
    if (key === sortKey) {
      setSortDir((dir) => (dir === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "query" ? "asc" : "desc");
    }
  };

  const sorted = useMemo(() => {
    if (!data?.queries) return [];
    const copy = [...data.queries];
    copy.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === "string") return sortDir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
      return sortDir === "asc" ? av - bv : bv - av;
    });
    return copy;
  }, [data, sortKey, sortDir]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 10,
        padding: "18px 20px",
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: 14,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)" }}>Query Intelligence</span>
        {data?.enabled && (
          <button className="button-secondary" onClick={handleReset} disabled={resetting} style={{ padding: "4px 10px", fontSize: 11 }}>
            {resetting ? "Resetting…" : "Reset query stats"}
          </button>
        )}
      </div>
      {error && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{error}</span>}

      {data && !data.enabled && (
        <div style={{ padding: "10px 12px", background: "var(--accent-tint)", borderRadius: 9 }}>
          <span style={{ fontSize: 12, color: "oklch(35% 0.1 255)", lineHeight: 1.5 }}>{data.message}</span>
        </div>
      )}

      {data && data.enabled && data.queries.length === 0 && (
        <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>No query activity recorded yet.</span>
      )}

      {data && data.enabled && data.queries.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr>
                {COLUMNS.map((col) => (
                  <th
                    key={col.key || "actions"}
                    onClick={() => handleSort(col.key)}
                    style={{
                      textAlign: "left",
                      padding: "6px 10px",
                      fontSize: 10.5,
                      fontWeight: 700,
                      textTransform: "uppercase",
                      letterSpacing: "0.02em",
                      color: "var(--text-muted)",
                      cursor: col.key ? "pointer" : "default",
                      userSelect: "none",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {col.label}
                    <HeaderHint text={col.help} />
                    {sortKey === col.key && col.key ? (sortDir === "asc" ? " ▲" : " ▼") : ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((q, i) => (
                <tr key={i}>
                  <td
                    title={q.query}
                    style={{
                      padding: "8px 10px",
                      fontFamily: "IBM Plex Mono, monospace",
                      color: "var(--text)",
                      maxWidth: 360,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {q.query}
                  </td>
                  <td style={{ padding: "8px 10px", color: "var(--text-secondary)" }}>{q.calls.toLocaleString()}</td>
                  <td style={{ padding: "8px 10px", color: "var(--text-secondary)" }}>{formatMs(q.total_exec_ms)}</td>
                  <td style={{ padding: "8px 10px", color: "var(--text-secondary)" }}>{formatMs(q.mean_exec_ms)}</td>
                  <td style={{ padding: "8px 10px", color: "var(--text-secondary)" }}>{q.rows.toLocaleString()}</td>
                  <td style={{ padding: "8px 10px" }}>
                    <div style={{ display: "flex", gap: 6 }}>
                      <button className="button-secondary" onClick={() => onExplain?.(q.query)}>
                        Load into Explain
                      </button>
                      <button className="button-secondary" onClick={() => onAnalyzeWithAi?.(q.query)}>
                        Analyze with AI
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
