import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { SearchCode } from "lucide-react";
import { api } from "./api";
import { formatMs } from "./format";
import HeaderHint from "./HeaderHint";
import { targetKey } from "./queryClient";
import { Alert, Card, EmptyState, Skeleton, SortHeader, useConfirm } from "./components";
import tableStyles from "./components/Table.module.css";
import styles from "./QueryIntelligence.module.css";

const POLL_INTERVAL_MS = 15000;
const COLUMNS = [
  {
    key: "query",
    label: "Query",
    help: "Normalized query text — literal values are replaced with $1, $2, ... by pg_stat_statements.",
  },
  { key: "calls", label: "Calls", numeric: true, help: "How many times this query has run since stats were last reset." },
  {
    key: "total_exec_ms",
    label: "Total",
    numeric: true,
    help: "Total time spent executing this query across all its calls combined — the best single number for \"which query costs the most.\"",
  },
  { key: "mean_exec_ms", label: "Mean", numeric: true, help: "Average execution time per call." },
  { key: "rows", label: "Rows", numeric: true, help: "Total rows returned or affected across all calls combined." },
];

export default function QueryIntelligence({ targetId, onExplain, onAnalyzeWithAi }) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const [resetting, setResetting] = useState(false);
  const [resetError, setResetError] = useState(null);
  const [sortKey, setSortKey] = useState("total_exec_ms");
  const [sortDir, setSortDir] = useState("desc");

  const statsKey = targetKey(targetId, "query-stats");
  const { data, error, isPending } = useQuery({
    queryKey: statsKey,
    queryFn: ({ signal }) => api.getQueryStats(targetId, { signal }),
    refetchInterval: POLL_INTERVAL_MS,
  });

  const handleReset = async () => {
    const ok = await confirm({
      title: "Reset query statistics?",
      message: "This clears pg_stat_statements for the whole server. The top-queries list will start empty again.",
      confirmLabel: "Reset stats",
      tone: "danger",
    });
    if (!ok) return;
    setResetting(true);
    setResetError(null);
    try {
      await api.resetQueryStats(targetId);
      await queryClient.invalidateQueries({ queryKey: statsKey });
    } catch (err) {
      setResetError(err.message);
    } finally {
      setResetting(false);
    }
  };

  const handleSort = (key) => {
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

  // The screen's <h1> already says "Query Intelligence", so the card is
  // named for what it lists.
  return (
    <Card
      title="Top queries"
      actions={
        data?.enabled && (
          <button type="button" className="button-secondary button-sm" onClick={handleReset} disabled={resetting}>
            {resetting ? "Resetting…" : "Reset query stats"}
          </button>
        )
      }
    >
      {error && <Alert tone="critical">{error.message}</Alert>}
      {resetError && <Alert tone="critical">{resetError}</Alert>}

      {isPending && <Skeleton lines={5} label="Loading query statistics…" />}

      {data && !data.enabled && (
        <Alert tone="info" compact>
          {data.message}
        </Alert>
      )}

      {data && data.enabled && data.queries.length === 0 && (
        <EmptyState icon={SearchCode} title="No query activity recorded yet." />
      )}

      {data && data.enabled && data.queries.length > 0 && (
        <div className={tableStyles.wrap}>
          <table className={tableStyles.table}>
            <thead>
              <tr>
                {COLUMNS.map((col) => (
                  <SortHeader
                    key={col.key}
                    column={col.key}
                    sortKey={sortKey}
                    sortDir={sortDir}
                    onSort={handleSort}
                    numeric={col.numeric}
                    hint={<HeaderHint text={col.help} />}
                  >
                    {col.label}
                  </SortHeader>
                ))}
                <th scope="col" className={tableStyles.th}>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody className={styles.body}>
              {sorted.map((q, i) => (
                <tr key={i} className={tableStyles.row}>
                  <td title={q.query} className={`${tableStyles.td} ${tableStyles.mono} ${styles.queryCell}`}>
                    {q.query}
                  </td>
                  <td className={`${tableStyles.td} ${tableStyles.numeric} ${styles.secondaryCell}`}>
                    {q.calls.toLocaleString()}
                  </td>
                  <td className={`${tableStyles.td} ${tableStyles.numeric} ${styles.secondaryCell}`}>
                    {formatMs(q.total_exec_ms)}
                  </td>
                  <td className={`${tableStyles.td} ${tableStyles.numeric} ${styles.secondaryCell}`}>
                    {formatMs(q.mean_exec_ms)}
                  </td>
                  <td className={`${tableStyles.td} ${tableStyles.numeric} ${styles.secondaryCell}`}>
                    {q.rows.toLocaleString()}
                  </td>
                  <td className={tableStyles.td}>
                    <div className={styles.rowActions}>
                      <button type="button" className="button-secondary button-sm" onClick={() => onExplain?.(q.query)}>
                        Load into Explain
                      </button>
                      <button
                        type="button"
                        className="button-secondary button-sm"
                        onClick={() => onAnalyzeWithAi?.(q.query)}
                      >
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
    </Card>
  );
}
