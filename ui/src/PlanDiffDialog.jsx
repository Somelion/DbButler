import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import styles from "./PlanDiffDialog.module.css";
import { api } from "./api";
import { targetKey } from "./queryClient";
import { Alert, Badge, Dialog, Field, Skeleton } from "./components";

// One line of a plan shape, as the fingerprint stores it: node type, join
// type, relation, index (app/plan_fingerprint.py — no costs).
function nodeLabel(node) {
  if (!node) return "";
  let label = node["Node Type"] ?? "?";
  if (node["Join Type"]) label = `${node["Join Type"]} ${label}`;
  if (node.Strategy && node.Strategy !== "Plain") label = `${label} (${node.Strategy})`;
  if (node["Index Name"]) label += ` using ${node["Index Name"]}`;
  if (node["Relation Name"]) label += ` on ${node["Relation Name"]}`;
  return label;
}

// Walks both trees in step (children aligned by position) into table rows:
// same | changed | removed (only before) | added (only after).
function diffRows(before, after, depth = 0, rows = []) {
  if (!before && !after) return rows;
  const left = nodeLabel(before);
  const right = nodeLabel(after);
  const status = !before ? "added" : !after ? "removed" : left === right ? "same" : "changed";
  rows.push({ depth, left, right, status });
  const leftChildren = before?.Plans ?? [];
  const rightChildren = after?.Plans ?? [];
  for (let i = 0; i < Math.max(leftChildren.length, rightChildren.length); i += 1) {
    diffRows(leftChildren[i], rightChildren[i], depth + 1, rows);
  }
  return rows;
}

// Labels are translation keys, translated at render.
const STATUS = {
  same: null,
  changed: { label: "diff.status.changed", tone: "attention" },
  removed: { label: "diff.status.removed", tone: "critical" },
  added: { label: "diff.status.added", tone: "healthy" },
};

const snapshotLabel = (snapshot) => new Date(snapshot.collected_at).toLocaleString();

// Side-by-side plan shapes for a plan-regression finding: by default the
// latest captured shape against the one before it; either side can be any
// recorded capture.
export default function PlanDiffDialog({ targetId, queryidText, onClose }) {
  const { t } = useTranslation("planRegressions");
  const open = Boolean(queryidText);
  const historyQuery = useQuery({
    queryKey: targetKey(targetId, "plan-history", queryidText),
    queryFn: ({ signal }) => api.getPlanHistory(targetId, queryidText, { signal }),
    enabled: open,
  });
  const [picked, setPicked] = useState({ before: 1, after: 0 });
  const plans = historyQuery.data?.plans ?? [];
  const before = plans[picked.before];
  const after = plans[picked.after];
  const rows = before || after ? diffRows(before?.plan, after?.plan) : [];
  const changedCount = rows.filter((r) => r.status !== "same").length;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="lg"
      title={t("diff.title")}
      description={t("diff.description")}
    >
      {historyQuery.error && <Alert tone="critical">{historyQuery.error.message}</Alert>}
      {historyQuery.isPending && open && <Skeleton lines={6} label={t("diff.loading")} />}
      {historyQuery.data && plans.length < 2 && (
        <Alert tone="info" compact>
          {t("diff.onlyOne")}
        </Alert>
      )}
      {plans.length >= 2 && (
        <>
          <code className={styles.query}>{historyQuery.data.query}</code>
          <div className={styles.pickers}>
            {["before", "after"].map((side) => (
              <Field key={side} label={side === "before" ? t("diff.before") : t("diff.after")}>
                <select
                  value={picked[side]}
                  onChange={(e) => setPicked((p) => ({ ...p, [side]: Number(e.target.value) }))}
                >
                  {plans.map((snapshot, index) => (
                    <option key={snapshot.fingerprint_hash + snapshot.collected_at} value={index}>
                      {snapshotLabel(snapshot)}
                      {index === 0 ? ` (${t("diff.latest")})` : ""}
                    </option>
                  ))}
                </select>
              </Field>
            ))}
          </div>
          <p className={styles.summary} role="status">
            {changedCount === 0 ? t("diff.identical") : t("diff.differ", { count: changedCount })}
          </p>
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th scope="col">{t("diff.before")}</th>
                  <th scope="col">{t("diff.after")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => (
                  <tr key={index} className={styles[row.status]}>
                    <td style={{ "--depth": row.depth }}>
                      <span className={styles.node}>{row.left || "—"}</span>
                    </td>
                    <td style={{ "--depth": row.depth }}>
                      <span className={styles.node}>{row.right || "—"}</span>
                      {STATUS[row.status] && (
                        <Badge tone={STATUS[row.status].tone} className={styles.badge}>
                          {t(STATUS[row.status].label)}
                        </Badge>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Dialog>
  );
}
