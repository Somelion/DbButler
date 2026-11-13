import { useQuery } from "@tanstack/react-query";
import { Trans, useTranslation } from "react-i18next";
import { Link } from "react-router";
import { X } from "lucide-react";
import styles from "./WorkloadSidePanel.module.css";
import { api } from "./api";
import { formatBytes, formatMs } from "./format";
import { targetKey } from "./queryClient";
import { screenPath } from "./routes";
import { Alert, Badge, SeverityBadge, Skeleton } from "./components";
import { EDGE_KINDS, SEVERITY_LABELS, isUnindexedFkEdge } from "./workloadKinds";

const formatCount = (value) => (value == null ? "—" : Math.round(value).toLocaleString());

function queryHistoryLink(targetId, queries) {
  const ids = queries.map((q) => q.queryid);
  return screenPath("query-history", targetId, null, { q: ids.slice(0, 6).join(","), focus: ids[0] });
}

function TopQueries({ targetId, queries, emptyText }) {
  const { t } = useTranslation("workloadMap");
  if (queries.length === 0) return <p className={styles.muted}>{emptyText}</p>;
  return (
    <>
      <ol className={styles.queryList}>
        {queries.map((q) => (
          <li key={q.queryid} className={styles.queryItem}>
            <code className={styles.queryText}>{q.query}</code>
            <span className={styles.queryMeta}>
              {t("panel.queryMeta", {
                time: formatMs(q.db_time_ms),
                calls: formatCount(q.calls),
                count: Math.round(q.calls ?? 0),
              })}
              {q.access_kinds?.length > 0 && ` · ${q.access_kinds.join(", ")}`}
              {q.filter_columns?.length > 0 &&
                ` · ${t("panel.filtersOn", { columns: q.filter_columns.join(", ") })}`}
            </span>
          </li>
        ))}
      </ol>
      <Link className="button-secondary button-sm" to={queryHistoryLink(targetId, queries)}>
        {t("panel.openQueryHistory")}
      </Link>
    </>
  );
}

function PanelHeader({ title, onClose, children }) {
  const { t } = useTranslation("workloadMap");
  return (
    <div className={styles.header}>
      <div className={styles.headerText}>
        <h2 className={styles.title}>{title}</h2>
        {children}
      </div>
      <button type="button" className="button-icon" onClick={onClose} aria-label={t("panel.close")}>
        <X size={16} aria-hidden="true" />
      </button>
    </div>
  );
}

function TablePanel({ targetId, hours, until, node, edges, onSelect, onClose }) {
  const { t } = useTranslation("workloadMap");
  // `until` follows the map's time slider, so a past map lists that
  // window's queries, not today's.
  const detailQuery = useQuery({
    queryKey: targetKey(targetId, "workload-table", node.schema, node.table, hours, until ?? "now"),
    queryFn: ({ signal }) => api.getWorkloadTable(targetId, node.schema, node.table, hours, { signal }, until),
  });
  const detail = detailQuery.data;
  const related = edges.filter((e) => e.source === node.id || e.target === node.id);
  const severityLabel = node.severity ? SEVERITY_LABELS[node.severity] : SEVERITY_LABELS.none;

  return (
    <>
      <PanelHeader title={node.id} onClose={onClose}>
        <SeverityBadge severity={node.severity ?? "healthy"} label={severityLabel} />
      </PanelHeader>

      <dl className={styles.stats}>
        <div>
          <dt>{t("panel.size")}</dt>
          <dd>{formatBytes(node.total_bytes)}</dd>
        </div>
        <div>
          <dt>{t("panel.liveRows")}</dt>
          <dd>{formatCount(node.live_rows)}</dd>
        </div>
        <div>
          <dt>{t("panel.deadRows")}</dt>
          <dd>{node.dead_pct == null ? "—" : `${node.dead_pct}%`}</dd>
        </div>
        <div>
          <dt>{t("panel.dbShare")}</dt>
          <dd>{node.share_pct}%</dd>
        </div>
        <div>
          <dt>{t("panel.seqRowsRead")}</dt>
          <dd>{formatCount(node.seq_tup_read)}</dd>
        </div>
        <div>
          <dt>{t("panel.rowsWritten")}</dt>
          <dd>{formatCount(node.writes)}</dd>
        </div>
        <div>
          <dt>{t("panel.hotUpdates")}</dt>
          <dd>{node.hot_update_pct == null ? "—" : `${node.hot_update_pct}%`}</dd>
        </div>
        {node.folded_children > 0 && (
          <div>
            <dt>{t("panel.partitions")}</dt>
            <dd>{node.folded_children}</dd>
          </div>
        )}
      </dl>

      <div className={styles.links}>
        <Link
          className="button-secondary button-sm"
          to={screenPath("table-health", targetId, null, { table: node.id })}
        >
          {t("panel.openTableHealth")}
        </Link>
        <Link className="button-secondary button-sm" to={screenPath("advisor", targetId, "index")}>
          {t("panel.indexAdvisor")}
        </Link>
      </div>

      <section className={styles.section} aria-labelledby="workload-related">
        <h3 id="workload-related" className={styles.sectionTitle}>
          {t("panel.relationships", { n: related.length })}
        </h3>
        {related.length === 0 ? (
          <p className={styles.muted}>{t("panel.notJoined")}</p>
        ) : (
          <ul className={styles.relatedList}>
            {related.map((edge) => {
              const other = edge.source === node.id ? edge.target : edge.source;
              return (
                <li key={edge.id}>
                  <button
                    type="button"
                    className={styles.relatedButton}
                    onClick={() => onSelect({ type: "edge", id: edge.id })}
                  >
                    <span className={styles.relatedName}>{other}</span>
                    <Badge tone={EDGE_KINDS[edge.kind].tone}>{EDGE_KINDS[edge.kind].label}</Badge>
                    {isUnindexedFkEdge(edge) && <Badge tone="attention">{t("panel.unindexed")}</Badge>}
                    {edge.kind !== "fk_unused" && <span className={styles.muted}>{edge.share_pct}%</span>}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {detailQuery.error && <Alert tone="critical">{detailQuery.error.message}</Alert>}
      {detailQuery.isPending && <Skeleton lines={5} label={t("panel.loadingDetails")} />}
      {detail && (
        <>
          <section className={styles.section} aria-labelledby="workload-queries">
            <h3 id="workload-queries" className={styles.sectionTitle}>
              {t("panel.tableQueries", { n: detail.query_count })}
            </h3>
            <TopQueries targetId={targetId} queries={detail.queries} emptyText={t("panel.noTableQueries")} />
          </section>

          <section className={styles.section} aria-labelledby="workload-indexes">
            <h3 id="workload-indexes" className={styles.sectionTitle}>
              {t("panel.indexes", { n: detail.indexes.length })}
            </h3>
            {detail.index_usage_partial && (
              <Alert tone="info" compact>
                {t("panel.hypertableNote")}
              </Alert>
            )}
            {detail.indexes.length === 0 ? (
              <p className={styles.muted}>{t("panel.noIndexes")}</p>
            ) : (
              <ul className={styles.indexList}>
                {detail.indexes.map((index) => (
                  <li key={index.name} className={styles.indexItem}>
                    <span className={styles.indexName}>
                      {index.name}
                      {index.is_primary && <Badge tone="info">{t("panel.primaryKey")}</Badge>}
                      {!index.is_primary && index.is_unique && <Badge tone="info">{t("panel.unique")}</Badge>}
                      {index.idx_scan === 0 && !index.is_primary && (
                        <Badge tone="attention">{t("panel.neverScanned")}</Badge>
                      )}
                    </span>
                    <span className={styles.queryMeta}>
                      {t("panel.indexMeta", {
                        scans: formatCount(index.idx_scan),
                        size: formatBytes(index.size_bytes),
                        count: Math.round(index.idx_scan ?? 0),
                      })}
                    </span>
                    <code className={styles.queryText}>{index.definition}</code>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className={styles.section} aria-labelledby="workload-findings">
            <h3 id="workload-findings" className={styles.sectionTitle}>
              {t("panel.openFindings", { n: detail.findings.length })}
            </h3>
            {detail.findings.length === 0 ? (
              <p className={styles.muted}>{t("panel.noFindings")}</p>
            ) : (
              <ul className={styles.findingList}>
                {detail.findings.map((finding) => (
                  <li key={finding.id} className={styles.findingItem}>
                    <SeverityBadge severity={finding.severity} />
                    <span>{finding.title ?? finding.id}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </>
  );
}

function EdgePanel({ targetId, edge, onSelect, onClose }) {
  const { t } = useTranslation("workloadMap");
  const kind = EDGE_KINDS[edge.kind];
  const fk = edge.foreign_keys[0];

  return (
    <>
      <PanelHeader
        title={
          <>
            {edge.source} <span aria-label={t("panel.and")}>⇄</span> {edge.target}
          </>
        }
        onClose={onClose}
      >
        <Badge tone={kind.tone}>{kind.label}</Badge>
      </PanelHeader>
      <p className={styles.description}>{kind.description}</p>

      <div className={styles.links}>
        <button
          type="button"
          className="button-secondary button-sm"
          onClick={() => onSelect({ type: "table", id: edge.source })}
        >
          {edge.source}
        </button>
        <button
          type="button"
          className="button-secondary button-sm"
          onClick={() => onSelect({ type: "table", id: edge.target })}
        >
          {edge.target}
        </button>
      </div>

      <section className={styles.section} aria-labelledby="workload-columns">
        <h3 id="workload-columns" className={styles.sectionTitle}>
          {t("panel.joinColumns")}
        </h3>
        <ul className={styles.columnList}>
          {edge.column_pairs.map(([left, right]) => (
            <li key={`${left}=${right}`}>
              <code>
                {edge.source}.{left} = {edge.target}.{right}
              </code>
            </li>
          ))}
        </ul>
        {edge.join_types.length > 0 && (
          <p className={styles.muted}>
            {t("panel.joinLine", {
              types: edge.join_types.join(", "),
              strategies: edge.join_strategies.join(", ") || t("panel.strategyNotShown"),
              share: edge.share_pct,
            })}
          </p>
        )}
      </section>

      {fk && (
        <section className={styles.section} aria-labelledby="workload-fk">
          <h3 id="workload-fk" className={styles.sectionTitle}>
            {t("panel.foreignKey")}
          </h3>
          <p>
            <code>{fk.name}</code>: {fk.child} ({fk.child_columns.join(", ")}) → {fk.parent} (
            {fk.parent_columns.join(", ")})
          </p>
          {fk.indexed ? (
            <Alert tone="healthy" compact>
              {t("panel.fkIndexed", { child: fk.child })}
            </Alert>
          ) : (
            <Alert tone="attention" compact>
              <Trans
                t={t}
                i18nKey="panel.fkNotIndexed"
                values={{ child: fk.child, parent: fk.parent, columns: fk.child_columns.join(", ") }}
                components={{ link: <Link to={screenPath("advisor", targetId, "index")} /> }}
              />
            </Alert>
          )}
        </section>
      )}

      {edge.kind === "no_fk" && (
        <section className={styles.section} aria-labelledby="workload-nofk">
          <h3 id="workload-nofk" className={styles.sectionTitle}>
            {t("panel.noFkTitle")}
          </h3>
          {/* One block per join condition: two different joins between the
              same tables (sender_id and recipient_id, say) are separate. */}
          {edge.column_groups
            .filter((group) => !group.foreign_key)
            .map((group) => (
              <div key={group.pairs.map((pair) => pair.join("=")).join(",")} className={styles.groupBlock}>
                <code>{group.pairs.map(([a, b]) => `${edge.source}.${a} = ${edge.target}.${b}`).join(" AND ")}</code>
                <ul className={styles.columnList}>
                  {[
                    [edge.source, group.source_indexed, 0],
                    [edge.target, group.target_indexed, 1],
                  ].map(([table, indexed, side]) => (
                    <li key={table}>
                      {table}:{" "}
                      {indexed ? (
                        t("panel.indexed")
                      ) : (
                        <>
                          <strong>{t("panel.noLeadingIndex")}</strong> —{" "}
                          <Link
                            to={screenPath("what-if", targetId, null, {
                              table,
                              columns: [...new Set(group.pairs.map((pair) => pair[side]))].join(","),
                            })}
                          >
                            {t("panel.costInWhatIf")}
                          </Link>
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          <p className={styles.muted}>{t("panel.noFkHint")}</p>
        </section>
      )}

      {edge.kind !== "fk_unused" && (
        <section className={styles.section} aria-labelledby="workload-edge-queries">
          <h3 id="workload-edge-queries" className={styles.sectionTitle}>
            {t("panel.edgeQueries", { n: edge.query_count })}
          </h3>
          <TopQueries targetId={targetId} queries={edge.top_queries} emptyText={t("panel.noEdgeQueries")} />
        </section>
      )}
    </>
  );
}

// Details for whatever is selected on the map — a table or a relationship.
export default function WorkloadSidePanel({ targetId, hours, until, graph, selection, onSelect, onClose }) {
  const { t } = useTranslation("workloadMap");
  const node = selection.type === "table" ? graph.nodes.find((n) => n.id === selection.id) : null;
  const edge = selection.type === "edge" ? graph.edges.find((e) => e.id === selection.id) : null;

  return (
    <aside className={styles.panel} aria-label={t("panel.label")}>
      {node && (
        <TablePanel
          key={node.id}
          targetId={targetId}
          hours={hours}
          until={until}
          node={node}
          edges={graph.edges}
          onSelect={onSelect}
          onClose={onClose}
        />
      )}
      {edge && <EdgePanel targetId={targetId} edge={edge} onSelect={onSelect} onClose={onClose} />}
      {!node && !edge && (
        <PanelHeader title={t("panel.notOnMap")} onClose={onClose}>
          <p className={styles.muted}>{t("panel.notOnMapHint")}</p>
        </PanelHeader>
      )}
    </aside>
  );
}
