import { useQuery } from "@tanstack/react-query";
import { Trans, useTranslation } from "react-i18next";
import { api } from "./api";
import { targetKey } from "./queryClient";
import HeaderHint from "./HeaderHint";
import { Alert, Badge, Card, CopyButton, Skeleton } from "./components";
import tableStyles from "./components/Table.module.css";
import styles from "./ConfigTuning.module.css";

// label/srLabel/help are translation keys, translated at render.
const COLUMNS = [
  { key: "parameter", label: "columns.parameter.label", help: "columns.parameter.help" },
  { key: "current", label: "columns.current.label", help: "columns.current.help" },
  { key: "recommended", label: "columns.recommended.label", help: "columns.recommended.help" },
  { key: "why", label: "columns.why.label", help: "columns.why.help" },
  { key: "restart", label: "", srLabel: "columns.restart.srLabel", help: "columns.restart.help" },
];

// Exported so HardwareProfile can invalidate it on save — the
// recommendations are recomputed from the profile server-side.
export const configTuningKey = (targetId) => targetKey(targetId, "config-tuning");

// `t` translates the script's two comment lines; the SQL stays as it is.
const alterSystemScript = (rows, t) =>
  rows.map((r) => `ALTER SYSTEM SET ${r.name} = '${r.recommended_value}';`).join("\n") +
  `\n\n-- ${t("script.reloadComment")}\nSELECT pg_reload_conf();\n` +
  `-- ${t("script.restartComment")}`;

const confSnippet = (rows) => rows.map((r) => `${r.name} = ${r.recommended_value}`).join("\n");

export default function ConfigTuning({ targetId }) {
  const { t } = useTranslation("configTuning");
  const { data, error, isPending } = useQuery({
    queryKey: configTuningKey(targetId),
    queryFn: ({ signal }) => api.getConfigTuning(targetId, { signal }),
  });

  // "Save a Hardware Profile first" isn't a failure, just the next step —
  // shown as a note rather than an error.
  const needsProfile = error?.message.includes("Hardware Profile");

  return (
    <Card title={t("title")}>
      <Alert tone="info" compact>
        <Trans t={t} i18nKey="recommendOnly" components={{ b: <b /> }} />
      </Alert>

      {error && (
        <Alert tone={needsProfile ? "info" : "critical"} compact>
          {error.message}
        </Alert>
      )}

      {isPending && <Skeleton lines={5} label={t("loading")} />}

      {data && (
        <>
          <div className={tableStyles.wrap}>
            <table className={tableStyles.table}>
              <thead>
                <tr>
                  {COLUMNS.map((col) => (
                    <th key={col.key} scope="col" className={tableStyles.th}>
                      {col.label && t(col.label)}
                      {col.srLabel && <span className="sr-only">{t(col.srLabel)}</span>}
                      <HeaderHint text={t(col.help)} />
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
                    <td className={tableStyles.td}>{row.requires_restart && <Badge tone="attention">{t("restartBadge")}</Badge>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className={styles.actions}>
            <CopyButton
              text={alterSystemScript(data.rows, t)}
              label={t("copyAlterSystem")}
              className="button-primary"
            />
            <CopyButton
              text={confSnippet(data.rows)}
              label={t("copyConf")}
              className="button-secondary"
            />
          </div>
        </>
      )}
    </Card>
  );
}
