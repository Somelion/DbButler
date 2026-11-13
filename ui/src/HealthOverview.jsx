import { useId } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { ArrowRight } from "lucide-react";
import { api } from "./api";
import { targetKey } from "./queryClient";
import { screenPath } from "./routes";
import { Alert, SEVERITY, SeverityIcon, Skeleton } from "./components";
import tone from "./components/tone.module.css";
import FindingCard from "./FindingCard";
import { useFirstSeenMap } from "./useFirstSeenMap";
import styles from "./HealthOverview.module.css";

const POLL_INTERVAL_MS = 15000;

// Translation keys (dashboard namespace), resolved at render time.
const OVERALL_LABEL = {
  healthy: "overall.healthy",
  attention: "overall.attention",
  critical: "overall.critical",
  unknown: "overall.unknown",
};

const EMPTY_HIDDEN = new Set();

// Where a tile's key or a live finding's category should jump to — covers
// both, since tile keys (routers/dashboard_settings.py::DASHBOARD_CATEGORIES)
// and finding categories mostly overlap but not always (Replication/Backup's
// findings are reused verbatim from their Advisor modules, so their
// `category` carries the " advisor" suffix the tile's own key doesn't).
// `advisorTab` becomes the Advisor URL's tab segment (/t/:id/advisor/:tab).
// The link label ("Open <screen>") comes from the view's screen name.
const CATEGORY_DESTINATIONS = {
  connections: { view: "activity" },
  locks: { view: "activity" },
  bloat: { view: "table-health" },
  cache: { view: "trends" },
  checkpoints: { view: "config-tuning" },
  wraparound: { view: "table-health" },
  autovacuum: { view: "table-health" },
  "xmin horizon": { view: "activity" },
  replication: { view: "advisor", advisorTab: "replication" },
  "replication advisor": { view: "advisor", advisorTab: "replication" },
  backup: { view: "advisor", advisorTab: "backup" },
  "backup advisor": { view: "advisor", advisorTab: "backup" },
  pooler: { view: "connections" },
  "query intelligence": { view: "query-intelligence" },
};

const destinationPath = (destination, targetId) =>
  destination ? screenPath(destination.view, targetId, destination.advisorTab) : undefined;

const destinationLabel = (destination, t) =>
  destination ? t("health.open", { screen: t(`common:screens.${destination.view}`) }) : undefined;

// Severity as a tone class name; anything unrecognised reads as "unknown".
const toneFor = (severity) => (SEVERITY[severity] ? severity : "unknown");

export default function HealthOverview({ targetId, hiddenCategories = EMPTY_HIDDEN }) {
  const { t } = useTranslation("dashboard");
  const queryClient = useQueryClient();
  const findingsHeadingId = useId();
  const [firstSeenByFindingId] = useFirstSeenMap(targetId);
  const dashboardKey = targetKey(targetId, "dashboard");

  // Polled through the query cache; the interval pauses in hidden tabs.
  const { data, error, isPending } = useQuery({
    queryKey: dashboardKey,
    queryFn: ({ signal }) => api.getDashboard(targetId, { signal }),
    refetchInterval: POLL_INTERVAL_MS,
  });

  // After a vacuum, refresh the dashboard straight away rather than waiting
  // for the next poll. A failed refresh is ignored (the poll will retry);
  // a failed vacuum rejects, so FindingCard can show the error.
  const handleVacuum = (finding) =>
    api
      .vacuumTable(targetId, finding.schema_name, finding.table_name, true)
      .then(() => queryClient.invalidateQueries({ queryKey: dashboardKey }));

  if (isPending) return <Skeleton lines={4} label={t("health.loading")} />;

  // A failed poll keeps the last good data on screen under the error.
  if (!data) {
    return <Alert tone="critical">{error?.message ?? t("health.loadError")}</Alert>;
  }

  const overallTone = toneFor(data.overall_severity);
  const visibleCategories = data.categories.filter((category) => !hiddenCategories.has(category.key));

  return (
    <div className={styles.root}>
      {error && <Alert tone="critical">{error.message}</Alert>}

      <div className={`${styles.overall} ${tone[overallTone]}`}>
        <SeverityIcon severity={overallTone} size={16} />
        <span className={styles.overallLabel}>{t(OVERALL_LABEL[data.overall_severity] ?? "overall.statusUnknown")}</span>
      </div>

      {visibleCategories.length > 0 && (
        <ul className={styles.tiles}>
          {visibleCategories.map((category) => (
            <CategoryTile key={category.key} category={category} targetId={targetId} />
          ))}
        </ul>
      )}

      {data.findings.length > 0 && (
        <section className={styles.findings} aria-labelledby={findingsHeadingId}>
          <h2 id={findingsHeadingId} className={styles.findingsTitle}>
            {t("health.findings")}
          </h2>
          {data.findings.map((finding) => {
            const destination = CATEGORY_DESTINATIONS[finding.category];
            return (
              <FindingCard
                key={finding.id}
                finding={finding}
                firstSeenAt={firstSeenByFindingId[finding.id]}
                navigateTo={destinationPath(destination, targetId)}
                navigateLabel={destinationLabel(destination, t)}
                onVacuum={finding.schema_name && finding.table_name ? () => handleVacuum(finding) : undefined}
              />
            );
          })}
        </section>
      )}
    </div>
  );
}

function CategoryTile({ category, targetId }) {
  const { t } = useTranslation("dashboard");
  const destination = CATEGORY_DESTINATIONS[category.key];
  return (
    <li className={styles.tile}>
      <div className={styles.tileHeader}>
        <span className={styles.tileLabel}>{category.label}</span>
        <SeverityIcon severity={toneFor(category.severity)} size={14} />
      </div>
      <span className={styles.tileValue}>{category.value}</span>
      <div className={styles.tileFooter}>
        <span className={styles.tileDetail}>{category.detail}</span>
        {destination && (
          <Link to={destinationPath(destination, targetId)} className={styles.tileLink}>
            {t("health.view")}
            <span className="sr-only"> {category.label}</span>
            <ArrowRight size={12} aria-hidden="true" />
          </Link>
        )}
      </div>
    </li>
  );
}
