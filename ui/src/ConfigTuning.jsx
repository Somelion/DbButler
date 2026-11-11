import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { targetKey } from "./queryClient";
import HeaderHint from "./HeaderHint";
import { Alert, Badge, Card, CopyButton, Skeleton } from "./components";
import tableStyles from "./components/Table.module.css";
import styles from "./ConfigTuning.module.css";

const COLUMNS = [
  { label: "Parameter", help: "The postgresql.conf setting name." },
  { label: "Current", help: "The value currently active on your target database." },
  {
    label: "Recommended",
    help: "DbButler's suggested value, sized from your saved Hardware Profile (RAM/CPU/storage/workload).",
  },
  { label: "Why", help: "The reasoning behind the recommendation." },
  {
    label: "",
    srLabel: "Restart required",
    help: "A badge here means this change only takes effect after a manual PostgreSQL restart — everything else applies via a reload (pg_reload_conf()).",
  },
];

// Exported so HardwareProfile can invalidate it on save — the
// recommendations are recomputed from the profile server-side.
export const configTuningKey = (targetId) => targetKey(targetId, "config-tuning");

const alterSystemScript = (rows) =>
  rows.map((r) => `ALTER SYSTEM SET ${r.name} = '${r.recommended_value}';`).join("\n") +
  "\n\n-- Reload-only settings take effect immediately after:\nSELECT pg_reload_conf();\n" +
  "-- Settings marked \"restart\" need a manual PostgreSQL restart to apply.";

const confSnippet = (rows) => rows.map((r) => `${r.name} = ${r.recommended_value}`).join("\n");

export default function ConfigTuning({ targetId }) {
  const { data, error, isPending } = useQuery({
    queryKey: configTuningKey(targetId),
    queryFn: ({ signal }) => api.getConfigTuning(targetId, { signal }),
  });

  // "Save a Hardware Profile first" isn't a failure, just the next step —
  // shown as a note rather than an error.
  const needsProfile = error?.message.includes("Hardware Profile");

  return (
    <Card title="Recommended settings">
      <Alert tone="info" compact>
        <b>Recommend only.</b> Nothing here changes your database automatically — copy or export these values and
        apply them yourself when you're ready.
      </Alert>

      {error && (
        <Alert tone={needsProfile ? "info" : "critical"} compact>
          {error.message}
        </Alert>
      )}

      {isPending && <Skeleton lines={5} label="Loading recommendations…" />}

      {data && (
        <>
          <div className={tableStyles.wrap}>
            <table className={tableStyles.table}>
              <thead>
                <tr>
                  {COLUMNS.map((col) => (
                    <th key={col.label || "restart"} scope="col" className={tableStyles.th}>
                      {col.label}
                      {col.srLabel && <span className="sr-only">{col.srLabel}</span>}
                      <HeaderHint text={col.help} />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => (
                  <tr key={row.name} className={tableStyles.row}>
                    <td className={`${tableStyles.td} ${styles.name}`}>{row.name}</td>
                    <td className={`${tableStyles.td} ${styles.current}`}>{row.current_value}</td>
                    <td className={`${tableStyles.td} ${styles.recommended}`}>{row.recommended_value}</td>
                    <td className={`${tableStyles.td} ${styles.why}`}>{row.why}</td>
                    <td className={tableStyles.td}>{row.requires_restart && <Badge tone="attention">Restart</Badge>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className={styles.actions}>
            <CopyButton
              text={alterSystemScript(data.rows)}
              label="Copy as ALTER SYSTEM script"
              className="button-primary"
            />
            <CopyButton
              text={confSnippet(data.rows)}
              label="Copy as postgresql.conf snippet"
              className="button-secondary"
            />
          </div>
        </>
      )}
    </Card>
  );
}
