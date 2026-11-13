import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
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

// label is a translation key, translated at render.
const VERDICTS = {
  recommended: { label: "verdicts.recommended", tone: "healthy" },
  mixed: { label: "verdicts.mixed", tone: "attention" },
  no_gain: { label: "verdicts.no_gain", tone: "neutral" },
  unused: { label: "verdicts.unused", tone: "neutral" },
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
  const { t } = useTranslation("whatIf");
  const [side, setSide] = useState("after");
  const plan = row ? JSON.stringify(side === "after" ? row.plan_after : row.plan_before) : "";
  return (
    <Dialog
      open={Boolean(row)}
      onClose={onClose}
      size="xl"
      title={t("planDialog.title")}
      description={t("planDialog.description")}
    >
      {row && (
        <>
          <SegmentedControl
            label={t("planDialog.whichPlan")}
            options={[
              { value: "before", label: t("planDialog.before", { cost: row.base_cost }) },
              { value: "after", label: t("planDialog.after", { cost: row.new_cost }) },
            ]}
            value={side}
            onChange={setSide}
          />
          <PlanViewerFrame
            plan={plan}
            query={row.query}
            title={side === "after" ? t("planDialog.frameTitleWith") : t("planDialog.frameTitleWithout")}
          />
        </>
      )}
    </Dialog>
  );
}

function CandidateResult({ result, onShowPlans }) {
  const { t } = useTranslation("whatIf");
  const verdict = VERDICTS[result.verdict] ?? VERDICTS.unused;
  return (
    <Card
      title={candidateLabel(result)}
      actions={<Badge tone={verdict.tone}>{t(verdict.label)}</Badge>}
    >
      {result.error ? (
        <Alert tone="critical">{result.error}</Alert>
      ) : (
        <>
          <div className={styles.stats}>
            <Stat label={t("stats.estimatedSize")} value={formatBytes(result.estimated_size_bytes)} />
            <Stat
              label={t("stats.weightedChange")}
              value={`${result.weighted_improvement_pct > 0 ? "−" : "+"}${Math.abs(result.weighted_improvement_pct)}%`}
              hint={t("stats.weightedChangeHint")}
              tone={result.weighted_improvement_pct >= 20 ? "healthy" : undefined}
            />
            <Stat label={t("stats.improved")} value={result.improved} />
            <Stat
              label={t("stats.regressed")}
              value={result.regressed}
              tone={result.regressed > 0 ? "attention" : undefined}
            />
            <Stat label={t("stats.unchanged")} value={result.unchanged} />
          </div>

          <div className={styles.ddl}>
            <code>{result.ddl}</code>
            <CopyButton text={result.ddl} />
          </div>

          {result.queries.length === 0 ? (
            <p className={styles.muted}>{t("noQueries")}</p>
          ) : (
            <div className={tableStyles.wrap}>
              <table className={tableStyles.table}>
                <caption className="sr-only">{t("queryTable.caption")}</caption>
                <thead>
                  <tr>
                    <th scope="col" className={tableStyles.th}>{t("queryTable.query")}</th>
                    <th scope="col" className={`${tableStyles.th} ${tableStyles.numeric}`}>{t("queryTable.dbTime")}</th>
                    <th scope="col" className={`${tableStyles.th} ${tableStyles.numeric}`}>
                      {t("queryTable.costToday")}
                    </th>
                    <th scope="col" className={`${tableStyles.th} ${tableStyles.numeric}`}>
                      {t("queryTable.withIndex")}
                    </th>
                    <th scope="col" className={`${tableStyles.th} ${tableStyles.numeric}`}>{t("queryTable.change")}</th>
                    <th scope="col" className={tableStyles.th}>{t("queryTable.plans")}</th>
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
                        {row.uses_index && <span className="sr-only"> {t("queryTable.usesIndex")}</span>}
                      </td>
                      <td className={tableStyles.td}>
                        {row.plan_before && row.plan_after ? (
                          <button type="button" className="button-link" onClick={() => onShowPlans(row)}>
                            {t("queryTable.comparePlans")}
                          </button>
                        ) : (
                          <span className={styles.muted}>{row.uses_index ? "—" : t("queryTable.indexNotUsed")}</span>
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
  const { t } = useTranslation("whatIf");
  const [searchParams] = useSearchParams();
  const [candidates, setCandidates] = useState(() => {
    const seeded = parseCandidate(searchParams.get("table") ?? "", searchParams.get("columns") ?? "");
    return seeded ? [seeded] : [];
  });
  const [tableText, setTableText] = useState("");
  const [columnsText, setColumnsText] = useState("");
  // A translation key, translated at render.
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
      setFormError("form.invalid");
      return;
    }
    if (candidates.some((c) => candidateLabel(c) === candidateLabel(candidate))) {
      setFormError("form.duplicate");
      return;
    }
    setFormError(null);
    setCandidates((list) => [...list, candidate].slice(0, MAX_CANDIDATES));
    setColumnsText("");
  };

  return (
    <div className={styles.screen}>
      <Card title={t("form.title")} description={t("form.description")}>
        <form className={styles.form} onSubmit={handleAdd}>
          <Field label={t("form.table")} hint={t("form.tableHint")}>
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
          <Field label={t("form.columns")} hint={t("form.columnsHint")}>
            <input value={columnsText} onChange={(e) => setColumnsText(e.target.value)} placeholder="customer_id, created_at" />
          </Field>
          <button type="submit" className="button-secondary" disabled={candidates.length >= MAX_CANDIDATES}>
            <Plus size={14} aria-hidden="true" />
            {t("form.add")}
          </button>
        </form>
        {formError && (
          <Alert tone="attention" compact>
            {t(formError)}
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
                  aria-label={t("form.remove", { name: candidateLabel(c) })}
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
            {run.isPending
              ? t("run.costing")
              : candidates.length === 0
                ? t("run.labelEmpty")
                : t("run.label", { count: candidates.length })}
          </button>
          <span className={styles.muted}>{t("run.note", { max: MAX_CANDIDATES })}</span>
        </div>
      </Card>

      {run.error && <Alert tone="critical">{run.error.message}</Alert>}

      {result && !result.hypopg_installed && (
        <Alert tone="attention" title={t("hypopgMissing")}>
          {result.message}
        </Alert>
      )}

      {result?.hypopg_installed && (
        <>
          <p className={styles.summary} role="status">
            {t("summary", { count: result.queries_considered })}
            {result.queries_skipped > 0 && ` ${t("summarySkipped", { count: result.queries_skipped })}`}.
          </p>
          <Alert tone="info" compact>
            {t("costNote")}
          </Alert>
          {result.candidates.map((candidate) => (
            <CandidateResult key={candidateLabel(candidate)} result={candidate} onShowPlans={setPlanRow} />
          ))}
        </>
      )}

      {!result && !run.isPending && candidates.length === 0 && (
        <Card>
          <EmptyState icon={Calculator} title={t("empty.title")}>
            {t("empty.body")}
          </EmptyState>
        </Card>
      )}

      <PlanCompareDialog row={planRow} onClose={() => setPlanRow(null)} />
    </div>
  );
}
