import { useId } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
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

const OVERALL_LABEL = {
  healthy: "All good",
  attention: "Needs attention",
  critical: "Needs attention now",
  unknown: "Collecting data…",
};

const EMPTY_HIDDEN = new Set();

// Where a tile's key or a live finding's category should jump to — covers
// both, since tile keys (routers/dashboard_settings.py::DASHBOARD_CATEGORIES)
// and finding categories mostly overlap but not always (Replication/Backup's
// findings are reused verbatim from their Advisor modules, so their
// `category` carries the " advisor" suffix the tile's own key doesn't).
// `advisorTab` becomes the Advisor URL's tab segment (/t/:id/advisor/:tab).
const CATEGORY_DESTINATIONS = {
  connections: { view: "activity", label: "Open Activity" },
  locks: { view: "activity", label: "Open Activity" },
  bloat: { view: "table-health", label: "Open Table Health" },
  cache: { view: "trends", label: "Open Trends" },
  checkpoints: { view: "config-tuning", label: "Open Config Tuning" },
  wraparound: { view: "table-health", label: "Open Table Health" },
  autovacuum: { view: "table-health", label: "Open Table Health" },
  "xmin horizon": { view: "activity", label: "Open Activity" },
  replication: { view: "advisor", advisorTab: "replication", label: "Open Advisor" },
  "replication advisor": { view: "advisor", advisorTab: "replication", label: "Open Advisor" },
  backup: { view: "advisor", advisorTab: "backup", label: "Open Advisor" },
  "backup advisor": { view: "advisor", advisorTab: "backup", label: "Open Advisor" },
  pooler: { view: "connections", label: "Open Connections" },
  "query intelligence": { view: "query-intelligence", label: "Open Query Intelligence" },
};

const destinationPath = (destination, targetId) =>
  destination ? screenPath(destination.view, targetId, destination.advisorTab) : undefined;

// Severity as a tone class name; anything unrecognised reads as "unknown".
const toneFor = (severity) => (SEVERITY[severity] ? severity : "unknown");

export default function HealthOverview({ targetId, hiddenCategories = EMPTY_HIDDEN }) {
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

  if (isPending) return <Skeleton lines={4} label="Loading health overview…" />;

  // A failed poll keeps the last good data on screen under the error.
  if (!data) {
    return <Alert tone="critical">{error?.message ?? "Couldn't load the health overview."}</Alert>;
  }

  const overallTone = toneFor(data.overall_severity);
  const visibleCategories = data.categories.filter((category) => !hiddenCategories.has(category.key));

  return (
    <div className={styles.root}>
      {error && <Alert tone="critical">{error.message}</Alert>}

      <div className={`${styles.overall} ${tone[overallTone]}`}>
        <SeverityIcon severity={overallTone} size={16} />
        <span className={styles.overallLabel}>{OVERALL_LABEL[data.overall_severity] ?? "Status unknown"}</span>
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
            Findings
          </h2>
          {data.findings.map((finding) => {
            const destination = CATEGORY_DESTINATIONS[finding.category];
            return (
              <FindingCard
                key={finding.id}
                finding={finding}
                firstSeenAt={firstSeenByFindingId[finding.id]}
                navigateTo={destinationPath(destination, targetId)}
                navigateLabel={destination?.label}
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
            View<span className="sr-only"> {category.label}</span>
            <ArrowRight size={12} aria-hidden="true" />
          </Link>
        )}
      </div>
    </li>
  );
}
