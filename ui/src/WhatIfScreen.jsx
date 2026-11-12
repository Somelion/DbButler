import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { Calculator, Plus, X } from "lucide-react";
import styles from "./WhatIfScreen.module.css";
import tableStyles from "./components/Table.module.css";
import { api } from "./api";
import { formatBytes, formatMs } from "./format";
import { targetKey } from "./queryClient";
import {
  Alert,
  Badge,
  Card,
  CopyButton,
  Dialog,
  EmptyState,
  Field,
  PlanViewerFrame,
  SegmentedControl,
  Stat,
} from "./components";

const MAX_CANDIDATES = 5;
const GRAPH_HOURS = 168;

const VERDICTS = {
  recommended: { label: "Recommended", tone: "healthy" },
  mixed: { label: "Mixed: helps some, hurts others", tone: "attention" },
  no_gain: { label: "Used, but little gain", tone: "neutral" },
  unused: { label: "Not used by any plan", tone: "neutral" },
};

function parseCandidate(tableText, columnsText) {
  const [schema, ...rest] = tableText.trim().split(".");
  const table = rest.join(".");
  const columns = columnsText
    .split(",")
    .map((c) => c.trim())
    .filter(Boolean);
  if (!schema || !table || columns.length === 0) return null;
  return { schema_name: schema, table_name: table, columns };
}

const candidateLabel = (c) => `${c.schema_name}.${c.table_name} (${c.columns.join(", ")})`;

// Before/after plans of one query, in pev2.
function PlanCompareDialog({ row, onClose }) {
  const [side, setSide] = useState("after");
  const plan = row ? JSON.stringify(side === "after" ? row.plan_after : row.plan_before) : "";
  return (
    <Dialog
      open={Boolean(row)}
      onClose={onClose}
      size="xl"
      title="Plan with and without the index"
      description="Planner estimates for a generic plan (EXPLAIN without ANALYZE) — nothing was executed."
    >
      {row && (
        <>
          <SegmentedControl
            label="Which plan"
            options={[
              { value: "before", label: `Today (cost ${row.base_cost})` },
              { value: "after", label: `With the index (cost ${row.new_cost})` },
            ]}
            value={side}
            onChange={setSide}
          />
          <PlanViewerFrame plan={plan} query={row.query} title={`Query plan, ${side === "after" ? "with" : "without"} the index`} />
        </>
      )}
    </Dialog>
  );
}

function CandidateResult({ result, onShowPlans }) {
  const verdict = VERDICTS[result.verdict] ?? VERDICTS.unused;
  return (
    <Card
      title={candidateLabel(result)}
      actions={<Badge tone={verdict.tone}>{verdict.label}</Badge>}
    >
      {result.error ? (
        <Alert tone="critical">{result.error}</Alert>
      ) : (
        <>
          <div className={styles.stats}>
            <Stat label="Estimated size" value={formatBytes(result.estimated_size_bytes)} />
            <Stat
              label="Weighted cost change"
              value={`${result.weighted_improvement_pct > 0 ? "−" : "+"}${Math.abs(result.weighted_improvement_pct)}%`}
              hint="Across the queries costed, weighted by their DB time"
              tone={result.weighted_improvement_pct >= 20 ? "healthy" : undefined}
            />
            <Stat label="Cheaper by ≥ 20%" value={result.improved} />
            <Stat label="Dearer by ≥ 10%" value={result.regressed} tone={result.regressed > 0 ? "attention" : undefined} />
            <Stat label="About the same" value={result.unchanged} />
          </div>

          <div className={styles.ddl}>
            <code>{result.ddl}</code>
            <CopyButton text={result.ddl} />
          </div>

          {result.queries.length === 0 ? (
            <p className={styles.muted}>No tracked query on this table could be costed.</p>
          ) : (
            <div className={tableStyles.wrap}>
              <table className={tableStyles.table}>
                <caption className="sr-only">Estimated cost of each tracked query, without and with the index</caption>
                <thead>
                  <tr>
                    <th scope="col" className={tableStyles.th}>Query</th>
                    <th scope="col" className={`${tableStyles.th} ${tableStyles.numeric}`}>DB time</th>
                    <th scope="col" className={`${tableStyles.th} ${tableStyles.numeric}`}>Cost today</th>
                    <th scope="col" className={`${tableStyles.th} ${tableStyles.numeric}`}>With index</th>
                    <th scope="col" className={`${tableStyles.th} ${tableStyles.numeric}`}>Change</th>
                    <th scope="col" className={tableStyles.th}>Plans</th>
                  </tr>
                </thead>
                <tbody>
                  {result.queries.map((row) => (
                    <tr key={row.queryid} className={tableStyles.row}>
                      <td className={`${tableStyles.td} ${tableStyles.mono} ${styles.queryCell}`}>{row.query}</td>
                      <td className={`${tableStyles.td} ${tableStyles.numeric}`}>{formatMs(row.db_time_ms)}</td>
                      <td className={`${tableStyles.td} ${tableStyles.numeric}`}>{row.base_cost.toLocaleString()}</td>
                      <td className={`${tableStyles.td} ${tableStyles.numeric}`}>{row.new_cost.toLocaleString()}</td>
                      <td className={`${tableStyles.td} ${tableStyles.numeric}`}>
                        <Badge tone={row.change_pct <= -20 ? "healthy" : row.change_pct >= 10 ? "attention" : "neutral"}>
                          {row.change_pct > 0 ? "+" : ""}
                          {row.change_pct}%
                        </Badge>
                        {row.uses_index && <span className="sr-only"> (plan uses the index)</span>}
                      </td>
                      <td className={tableStyles.td}>
                        {row.plan_before && row.plan_after ? (
                          <button type="button" className="button-link" onClick={() => onShowPlans(row)}>
                            Compare plans
                          </button>
                        ) : (
                          <span className={styles.muted}>{row.uses_index ? "—" : "index not used"}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </Card>
  );
}

// What-if Indexes: cost candidate indexes against the tracked workload with
// HypoPG, without building anything. A finding's "Cost in What-if" link
// arrives as ?table=schema.table&columns=a,b and becomes the first candidate.
export default function WhatIfScreen({ targetId }) {
  const [searchParams] = useSearchParams();
  const [candidates, setCandidates] = useState(() => {
    const seeded = parseCandidate(searchParams.get("table") ?? "", searchParams.get("columns") ?? "");
    return seeded ? [seeded] : [];
  });
  const [tableText, setTableText] = useState("");
  const [columnsText, setColumnsText] = useState("");
  const [formError, setFormError] = useState(null);
  const [planRow, setPlanRow] = useState(null);

  // Table suggestions from the Workload Map (same cache entry).
  const graphQuery = useQuery({
    queryKey: targetKey(targetId, "workload-graph", GRAPH_HOURS),
    queryFn: ({ signal }) => api.getWorkloadGraph(targetId, GRAPH_HOURS, { signal }),
  });

  const run = useMutation({ mutationFn: (list) => api.runWhatIf(targetId, list, GRAPH_HOURS) });
  const result = run.data;

  const handleAdd = (event) => {
    event.preventDefault();
    const candidate = parseCandidate(tableText, columnsText);
    if (!candidate) {
      setFormError("Enter the table as schema.table and at least one column.");
      return;
    }
    if (candidates.some((c) => candidateLabel(c) === candidateLabel(candidate))) {
      setFormError("That candidate is already in the list.");
      return;
    }
    setFormError(null);
    setCandidates((list) => [...list, candidate].slice(0, MAX_CANDIDATES));
    setColumnsText("");
  };

  return (
    <div className={styles.screen}>
      <Card
        title="Candidate indexes"
        description="Each candidate is created as a hypothetical index (HypoPG) in a private session, and the tracked queries that touch its table are planned with and without it. Nothing is built or written on the database."
      >
        <form className={styles.form} onSubmit={handleAdd}>
          <Field label="Table" hint="schema.table">
            <input
              list="what-if-tables"
              value={tableText}
              onChange={(e) => setTableText(e.target.value)}
              placeholder="public.orders"
            />
          </Field>
          <datalist id="what-if-tables">
            {graphQuery.data?.nodes.map((n) => (
              <option key={n.id} value={n.id} />
            ))}
          </datalist>
          <Field label="Columns" hint="In index order, comma-separated">
            <input value={columnsText} onChange={(e) => setColumnsText(e.target.value)} placeholder="customer_id, created_at" />
          </Field>
          <button type="submit" className="button-secondary" disabled={candidates.length >= MAX_CANDIDATES}>
            <Plus size={14} aria-hidden="true" />
            Add candidate
          </button>
        </form>
        {formError && (
          <Alert tone="attention" compact>
            {formError}
          </Alert>
        )}

        {candidates.length > 0 && (
          <ul className={styles.candidates}>
            {candidates.map((c) => (
              <li key={candidateLabel(c)} className={styles.candidate}>
                <code>{candidateLabel(c)}</code>
                <button
                  type="button"
                  className="button-icon"
                  aria-label={`Remove ${candidateLabel(c)}`}
                  onClick={() => setCandidates((list) => list.filter((x) => x !== c))}
                >
                  <X size={14} aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        )}

        <div className={styles.runRow}>
          <button
            type="button"
            className="button-primary"
            disabled={candidates.length === 0 || run.isPending}
            onClick={() => run.mutate(candidates)}
          >
            <Calculator size={14} aria-hidden="true" />
            {run.isPending ? "Costing…" : `Cost ${candidates.length || ""} candidate${candidates.length === 1 ? "" : "s"}`}
          </button>
          <span className={styles.muted}>Up to {MAX_CANDIDATES} at a time. Costs are the planner's estimates, not timings.</span>
        </div>
      </Card>

      {run.error && <Alert tone="critical">{run.error.message}</Alert>}

      {result && !result.hypopg_installed && (
        <Alert tone="attention" title="HypoPG isn't installed">
          {result.message}
        </Alert>
      )}

      {result?.hypopg_installed && (
        <>
          <p className={styles.summary} role="status">
            Costed against {result.queries_considered} tracked quer{result.queries_considered === 1 ? "y" : "ies"}
            {result.queries_skipped > 0 &&
              ` (${result.queries_skipped} couldn't be planned — utility statements, or parameterized queries on PostgreSQL < 16)`}
            .
          </p>
          <Alert tone="info" compact>
            Planner cost leaves two things out: the work every write spends keeping the index up to date, and the
            foreign-key checks a DELETE or key UPDATE on a parent table makes through an index on the child&apos;s FK
            columns. An index on FK columns can be essential even when no plan here picks it.
          </Alert>
          {result.candidates.map((candidate) => (
            <CandidateResult key={candidateLabel(candidate)} result={candidate} onShowPlans={setPlanRow} />
          ))}
        </>
      )}

      {!result && !run.isPending && candidates.length === 0 && (
        <Card>
          <EmptyState icon={Calculator} title="No candidates yet">
            Add an index above, or use "Cost in What-if" on an Index Advisor or Refactoring finding.
          </EmptyState>
        </Card>
      )}

      <PlanCompareDialog row={planRow} onClose={() => setPlanRow(null)} />
    </div>
  );
}
