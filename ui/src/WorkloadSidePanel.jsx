import { useQuery } from "@tanstack/react-query";
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
  if (queries.length === 0) return <p className={styles.muted}>{emptyText}</p>;
  return (
    <>
      <ol className={styles.queryList}>
        {queries.map((q) => (
          <li key={q.queryid} className={styles.queryItem}>
            <code className={styles.queryText}>{q.query}</code>
            <span className={styles.queryMeta}>
              {formatMs(q.db_time_ms)} DB time · {formatCount(q.calls)} calls
              {q.access_kinds?.length > 0 && ` · ${q.access_kinds.join(", ")}`}
              {q.filter_columns?.length > 0 && ` · filters on ${q.filter_columns.join(", ")}`}
            </span>
          </li>
        ))}
      </ol>
      <Link className="button-secondary button-sm" to={queryHistoryLink(targetId, queries)}>
        Open in Query History
      </Link>
    </>
  );
}

function PanelHeader({ title, onClose, children }) {
  return (
    <div className={styles.header}>
      <div className={styles.headerText}>
        <h2 className={styles.title}>{title}</h2>
        {children}
      </div>
      <button type="button" className="button-icon" onClick={onClose} aria-label="Close details">
        <X size={16} aria-hidden="true" />
      </button>
    </div>
  );
}

function TablePanel({ targetId, hours, node, edges, onSelect, onClose }) {
  const detailQuery = useQuery({
    queryKey: targetKey(targetId, "workload-table", node.schema, node.table, hours),
    queryFn: ({ signal }) => api.getWorkloadTable(targetId, node.schema, node.table, hours, { signal }),
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
          <dt>Size</dt>
          <dd>{formatBytes(node.total_bytes)}</dd>
        </div>
        <div>
          <dt>Live rows</dt>
          <dd>{formatCount(node.live_rows)}</dd>
        </div>
        <div>
          <dt>Dead rows</dt>
          <dd>{node.dead_pct == null ? "—" : `${node.dead_pct}%`}</dd>
        </div>
        <div>
          <dt>Share of DB time</dt>
          <dd>{node.share_pct}%</dd>
        </div>
        <div>
          <dt>Seq-scan rows read</dt>
          <dd>{formatCount(node.seq_tup_read)}</dd>
        </div>
        <div>
          <dt>Rows written</dt>
          <dd>{formatCount(node.writes)}</dd>
        </div>
        <div>
          <dt>HOT updates</dt>
          <dd>{node.hot_update_pct == null ? "—" : `${node.hot_update_pct}%`}</dd>
        </div>
        {node.folded_children > 0 && (
          <div>
            <dt>Partitions / chunks</dt>
            <dd>{node.folded_children}</dd>
          </div>
        )}
      </dl>

      <div className={styles.links}>
        <Link
          className="button-secondary button-sm"
          to={screenPath("table-health", targetId, null, { table: node.id })}
        >
          Open in Table Health
        </Link>
        <Link className="button-secondary button-sm" to={screenPath("advisor", targetId, "index")}>
          Index Advisor
        </Link>
      </div>

      <section className={styles.section} aria-labelledby="workload-related">
        <h3 id="workload-related" className={styles.sectionTitle}>
          Relationships ({related.length})
        </h3>
        {related.length === 0 ? (
          <p className={styles.muted}>Not joined with any other table in the tracked queries.</p>
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
                    {isUnindexedFkEdge(edge) && <Badge tone="attention">unindexed</Badge>}
                    {edge.kind !== "fk_unused" && <span className={styles.muted}>{edge.share_pct}%</span>}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {detailQuery.error && <Alert tone="critical">{detailQuery.error.message}</Alert>}
      {detailQuery.isPending && <Skeleton lines={5} label="Loading table details…" />}
      {detail && (
        <>
          <section className={styles.section} aria-labelledby="workload-queries">
            <h3 id="workload-queries" className={styles.sectionTitle}>
              Queries touching this table ({detail.query_count})
            </h3>
            <TopQueries
              targetId={targetId}
              queries={detail.queries}
              emptyText="No tracked query touches this table — it's on the map through a foreign key."
            />
          </section>

          <section className={styles.section} aria-labelledby="workload-indexes">
            <h3 id="workload-indexes" className={styles.sectionTitle}>
              Indexes ({detail.indexes.length})
            </h3>
            {detail.index_usage_partial && (
              <Alert tone="info" compact>
                TimescaleDB hypertable: scans of the per-chunk indexes aren't included in these counts.
              </Alert>
            )}
            {detail.indexes.length === 0 ? (
              <p className={styles.muted}>No indexes.</p>
            ) : (
              <ul className={styles.indexList}>
                {detail.indexes.map((index) => (
                  <li key={index.name} className={styles.indexItem}>
                    <span className={styles.indexName}>
                      {index.name}
                      {index.is_primary && <Badge tone="info">primary key</Badge>}
                      {!index.is_primary && index.is_unique && <Badge tone="info">unique</Badge>}
                      {index.idx_scan === 0 && !index.is_primary && <Badge tone="attention">never scanned</Badge>}
                    </span>
                    <span className={styles.queryMeta}>
                      {formatCount(index.idx_scan)} scans · {formatBytes(index.size_bytes)}
                    </span>
                    <code className={styles.queryText}>{index.definition}</code>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className={styles.section} aria-labelledby="workload-findings">
            <h3 id="workload-findings" className={styles.sectionTitle}>
              Open findings ({detail.findings.length})
            </h3>
            {detail.findings.length === 0 ? (
              <p className={styles.muted}>None from the last deep scan.</p>
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
  const kind = EDGE_KINDS[edge.kind];
  const fk = edge.foreign_keys[0];

  return (
    <>
      <PanelHeader
        title={
          <>
            {edge.source} <span aria-label="and">⇄</span> {edge.target}
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
          Join columns
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
            {edge.join_types.join(", ")} join · {edge.join_strategies.join(", ") || "strategy not shown"} ·{" "}
            {edge.share_pct}% of tracked DB time
          </p>
        )}
      </section>

      {fk && (
        <section className={styles.section} aria-labelledby="workload-fk">
          <h3 id="workload-fk" className={styles.sectionTitle}>
            Foreign key
          </h3>
          <p>
            <code>{fk.name}</code>: {fk.child} ({fk.child_columns.join(", ")}) → {fk.parent} (
            {fk.parent_columns.join(", ")})
          </p>
          {fk.indexed ? (
            <Alert tone="healthy" compact>
              The FK columns on {fk.child} are indexed.
            </Alert>
          ) : (
            <Alert tone="attention" compact>
              No index on {fk.child} ({fk.child_columns.join(", ")}). Joins from {fk.parent}, and every DELETE or key
              update on {fk.parent}, have to scan {fk.child}.{" "}
              <Link to={screenPath("advisor", targetId, "index")}>See Index Advisor</Link>.
            </Alert>
          )}
        </section>
      )}

      {edge.kind === "no_fk" && (
        <section className={styles.section} aria-labelledby="workload-nofk">
          <h3 id="workload-nofk" className={styles.sectionTitle}>
            Join conditions without a foreign key
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
                        "indexed"
                      ) : (
                        <>
                          <strong>no index leads with the join column</strong> —{" "}
                          <Link
                            to={screenPath("what-if", targetId, null, {
                              table,
                              columns: [...new Set(group.pairs.map((pair) => pair[side]))].join(","),
                            })}
                          >
                            cost an index in What-if
                          </Link>
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          <p className={styles.muted}>
            If one side is meant to reference the other, declaring the foreign key documents it and lets PostgreSQL
            enforce it. A side without an index is scanned on every join.
          </p>
        </section>
      )}

      {edge.kind !== "fk_unused" && (
        <section className={styles.section} aria-labelledby="workload-edge-queries">
          <h3 id="workload-edge-queries" className={styles.sectionTitle}>
            Queries joining these tables ({edge.query_count})
          </h3>
          <TopQueries targetId={targetId} queries={edge.top_queries} emptyText="No tracked query joins these tables." />
        </section>
      )}
    </>
  );
}

// Details for whatever is selected on the map — a table or a relationship.
export default function WorkloadSidePanel({ targetId, hours, graph, selection, onSelect, onClose }) {
  const node = selection.type === "table" ? graph.nodes.find((n) => n.id === selection.id) : null;
  const edge = selection.type === "edge" ? graph.edges.find((e) => e.id === selection.id) : null;

  return (
    <aside className={styles.panel} aria-label="Selection details">
      {node && (
        <TablePanel
          key={node.id}
          targetId={targetId}
          hours={hours}
          node={node}
          edges={graph.edges}
          onSelect={onSelect}
          onClose={onClose}
        />
      )}
      {edge && <EdgePanel targetId={targetId} edge={edge} onSelect={onSelect} onClose={onClose} />}
      {!node && !edge && (
        <PanelHeader title="Not on the map" onClose={onClose}>
          <p className={styles.muted}>The selected item isn't part of this time range's map.</p>
        </PanelHeader>
      )}
    </aside>
  );
}
