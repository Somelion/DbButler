import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { Download, FileText, Printer, RefreshCw } from "lucide-react";
import styles from "./ReportsScreen.module.css";
import tableStyles from "./components/Table.module.css";
import { api } from "./api";
import { formatRelativeTime } from "./format";
import { targetKey } from "./queryClient";
import { Alert, Badge, Card, EmptyState, SeverityBadge, Skeleton } from "./components";

const period = (report, dateFormat) =>
  `${dateFormat.format(new Date(report.period_start))} – ${dateFormat.format(new Date(report.period_end))}`;

function downloadHtml(html, fileName) {
  const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

// Weekly Reports: one page per week — findings, metrics, heaviest queries,
// lock incidents, plan changes, growth and server events — generated every
// Monday (Settings → Scheduler) or on demand, viewable, printable to PDF
// and downloadable as a self-contained HTML file.
export default function ReportsScreen({ targetId, target }) {
  const { t, i18n } = useTranslation("reports");
  const dateFormat = new Intl.DateTimeFormat(i18n.language, { day: "numeric", month: "short", year: "numeric" });
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const frameRef = useRef(null);
  const listKey = targetKey(targetId, "reports");

  const listQuery = useQuery({ queryKey: listKey, queryFn: ({ signal }) => api.getReports(targetId, { signal }) });
  const reports = listQuery.data?.reports ?? [];
  const activeId = searchParams.get("report") ?? reports[0]?.id ?? null;
  const active = reports.find((r) => r.id === activeId) ?? null;

  // A stored report never changes: its page is cached for good.
  const htmlQuery = useQuery({
    queryKey: ["report-html", activeId],
    queryFn: ({ signal }) => api.getReportHtml(activeId, { signal }),
    enabled: Boolean(activeId),
    staleTime: Infinity,
  });

  const select = (id) =>
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("report", id);
        return next;
      },
      { replace: true }
    );

  const generate = useMutation({
    mutationFn: () => api.createReport(targetId),
    onSuccess: (summary) => {
      queryClient.invalidateQueries({ queryKey: listKey });
      select(summary.id);
    },
  });

  const fileName = active
    ? `dbbutler-report-${(target?.name ?? "target").replace(/[^A-Za-z0-9._-]+/g, "-")}-${active.period_end.slice(0, 10)}.html`
    : "report.html";

  return (
    <div className={styles.screen}>
      <Card
        title={t("title")}
        description={t("description")}
        actions={
          <button type="button" className="button-secondary" onClick={() => generate.mutate()} disabled={generate.isPending}>
            <RefreshCw size={14} aria-hidden="true" />
            {generate.isPending ? t("generating") : t("generate")}
          </button>
        }
        flush
      >
        {generate.error && (
          <Alert tone="critical" compact>
            {generate.error.message}
          </Alert>
        )}
        {listQuery.isPending ? (
          <Skeleton lines={4} label={t("loadingList")} />
        ) : listQuery.error ? (
          <Alert tone="critical">{listQuery.error.message}</Alert>
        ) : reports.length === 0 ? (
          <EmptyState icon={FileText} title={t("emptyTitle")}>
            {t("emptyHint")}
          </EmptyState>
        ) : (
          <div className={tableStyles.wrap}>
            <table className={tableStyles.table}>
              <caption className="sr-only">{t("caption")}</caption>
              <thead>
                <tr>
                  <th scope="col" className={tableStyles.th}>{t("columns.week")}</th>
                  <th scope="col" className={tableStyles.th}>{t("columns.status")}</th>
                  <th scope="col" className={`${tableStyles.th} ${tableStyles.numeric}`}>{t("columns.openCriticalAttention")}</th>
                  <th scope="col" className={`${tableStyles.th} ${tableStyles.numeric}`}>{t("columns.newResolved")}</th>
                  <th scope="col" className={`${tableStyles.th} ${tableStyles.numeric}`}>{t("columns.lockIncidents")}</th>
                  <th scope="col" className={tableStyles.th}>{t("columns.generated")}</th>
                </tr>
              </thead>
              <tbody>
                {reports.map((report) => (
                  <tr
                    key={report.id}
                    className={`${tableStyles.row} ${report.id === activeId ? styles.activeRow : ""}`}
                    aria-current={report.id === activeId ? "true" : undefined}
                  >
                    <td className={tableStyles.td}>
                      <button type="button" className="button-link" onClick={() => select(report.id)}>
                        {period(report, dateFormat)}
                      </button>
                    </td>
                    <td className={tableStyles.td}>
                      <SeverityBadge severity={report.overall_severity} />
                    </td>
                    <td className={`${tableStyles.td} ${tableStyles.numeric}`}>
                      {report.open_critical} / {report.open_attention}
                    </td>
                    <td className={`${tableStyles.td} ${tableStyles.numeric}`}>
                      {report.opened} / {report.resolved}
                    </td>
                    <td className={`${tableStyles.td} ${tableStyles.numeric}`}>{report.lock_incidents}</td>
                    <td className={tableStyles.td}>
                      {formatRelativeTime(report.generated_at)}{" "}
                      {report.trigger === "manual" && <Badge tone="neutral">{t("onDemand")}</Badge>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {active && (
        <Card
          title={t("reportTitle", { period: period(active, dateFormat) })}
          actions={
            <>
              <button
                type="button"
                className="button-secondary"
                disabled={!htmlQuery.data}
                onClick={() => frameRef.current?.contentWindow?.print()}
              >
                <Printer size={14} aria-hidden="true" />
                {t("print")}
              </button>
              <button
                type="button"
                className="button-secondary"
                disabled={!htmlQuery.data}
                onClick={() => downloadHtml(htmlQuery.data, fileName)}
              >
                <Download size={14} aria-hidden="true" />
                {t("download")}
              </button>
            </>
          }
        >
          {htmlQuery.error ? (
            <Alert tone="critical">{htmlQuery.error.message}</Alert>
          ) : !htmlQuery.data ? (
            <Skeleton height={480} label={t("loadingReport")} />
          ) : (
            // No scripts run inside: the page is static HTML and CSS. Same
            // origin only so the Print button can reach its window.
            <iframe
              ref={frameRef}
              className={styles.frame}
              title={t("frameTitle", { period: period(active, dateFormat) })}
              sandbox="allow-same-origin allow-modals"
              srcDoc={htmlQuery.data}
            />
          )}
        </Card>
      )}
    </div>
  );
}
