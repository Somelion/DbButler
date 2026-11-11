import { useState } from "react";
import styles from "./WorkloadRelationsTable.module.css";
import tableStyles from "./components/Table.module.css";
import { Badge, SortHeader } from "./components";
import { EDGE_KINDS, isUnindexedFkEdge } from "./workloadKinds";

const KIND_ORDER = { no_fk: 0, fk: 1, fk_unused: 2 };

const SORTERS = {
  tables: (a, b) => `${a.source}|${a.target}`.localeCompare(`${b.source}|${b.target}`),
  kind: (a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind],
  share: (a, b) => a.share_pct - b.share_pct,
  queries: (a, b) => a.query_count - b.query_count,
};

// The map as a table — the same relationships, readable by keyboard and
// screen reader, sortable, and easier to scan for "every joined-without-FK
// pair" than the picture.
export default function WorkloadRelationsTable({ edges, selectedId, onSelect }) {
  const [sortKey, setSortKey] = useState("share");
  const [sortDir, setSortDir] = useState("desc");

  const handleSort = (key) => {
    if (key === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setSortDir(key === "tables" ? "asc" : "desc");
    }
  };

  const sorted = [...edges].sort((a, b) => (sortDir === "asc" ? 1 : -1) * SORTERS[sortKey](a, b));

  return (
    <div className={tableStyles.wrap}>
      <table className={tableStyles.table}>
        <caption className="sr-only">Relationships between tables, from joins in the tracked queries and declared foreign keys</caption>
        <thead>
          <tr>
            <SortHeader column="tables" sortKey={sortKey} sortDir={sortDir} onSort={handleSort}>
              Tables
            </SortHeader>
            <SortHeader column="kind" sortKey={sortKey} sortDir={sortDir} onSort={handleSort}>
              Kind
            </SortHeader>
            <th scope="col" className={tableStyles.th}>
              Join columns
            </th>
            <SortHeader column="share" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} numeric>
              DB time
            </SortHeader>
            <SortHeader column="queries" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} numeric>
              Queries
            </SortHeader>
            <th scope="col" className={tableStyles.th}>
              Indexes
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((edge) => (
            <tr
              key={edge.id}
              className={`${tableStyles.row} ${edge.id === selectedId ? tableStyles.selected : ""}`}
            >
              <td className={tableStyles.td}>
                <button
                  type="button"
                  className={styles.pairButton}
                  aria-pressed={edge.id === selectedId}
                  onClick={() => onSelect({ type: "edge", id: edge.id })}
                >
                  {edge.source} <span aria-label="and">⇄</span> {edge.target}
                </button>
              </td>
              <td className={tableStyles.td}>
                <Badge tone={EDGE_KINDS[edge.kind].tone}>{EDGE_KINDS[edge.kind].label}</Badge>
              </td>
              <td className={`${tableStyles.td} ${tableStyles.mono}`}>
                {edge.column_pairs.map(([a, b]) => `${a} = ${b}`).join(", ")}
              </td>
              <td className={`${tableStyles.td} ${tableStyles.numeric}`}>
                {edge.kind === "fk_unused" ? "—" : `${edge.share_pct}%`}
              </td>
              <td className={`${tableStyles.td} ${tableStyles.numeric}`}>{edge.query_count}</td>
              <td className={tableStyles.td}>
                {isUnindexedFkEdge(edge) ? (
                  <Badge tone="attention">FK unindexed</Badge>
                ) : edge.kind === "no_fk" && !(edge.source_indexed && edge.target_indexed) ? (
                  <Badge tone="attention">
                    {!edge.source_indexed && !edge.target_indexed ? "neither side" : "one side"} unindexed
                  </Badge>
                ) : (
                  <span className={styles.ok}>indexed</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
