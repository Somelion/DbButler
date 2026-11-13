import { useState } from "react";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation("workloadMap");
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
        <caption className="sr-only">{t("table.caption")}</caption>
        <thead>
          <tr>
            <SortHeader column="tables" sortKey={sortKey} sortDir={sortDir} onSort={handleSort}>
              {t("table.tables")}
            </SortHeader>
            <SortHeader column="kind" sortKey={sortKey} sortDir={sortDir} onSort={handleSort}>
              {t("table.kind")}
            </SortHeader>
            <th scope="col" className={tableStyles.th}>
              {t("table.joinColumns")}
            </th>
            <SortHeader column="share" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} numeric>
              {t("table.dbTime")}
            </SortHeader>
            <SortHeader column="queries" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} numeric>
              {t("table.queries")}
            </SortHeader>
            <th scope="col" className={tableStyles.th}>
              {t("table.indexes")}
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
                  {edge.source} <span aria-label={t("panel.and")}>⇄</span> {edge.target}
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
                  <Badge tone="attention">{t("table.fkUnindexed")}</Badge>
                ) : edge.kind === "no_fk" && !(edge.source_indexed && edge.target_indexed) ? (
                  <Badge tone="attention">
                    {!edge.source_indexed && !edge.target_indexed
                      ? t("table.neitherSideUnindexed")
                      : t("table.oneSideUnindexed")}
                  </Badge>
                ) : (
                  <span className={styles.ok}>{t("table.indexed")}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
