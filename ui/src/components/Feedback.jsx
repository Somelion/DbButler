import { severityMeta } from "./Badge";
import styles from "./Feedback.module.css";
import tone from "./tone.module.css";

// Banner for errors, warnings and notes.
//   tone: critical | attention | info | healthy
// A critical alert is role="alert" (announced immediately); the rest are
// role="status" (announced politely).
export function Alert({ tone: toneName = "info", title, compact = false, icon = true, className, children }) {
  const { Icon } = severityMeta(toneName);
  const classes = [styles.alert, tone[toneName] ?? tone.info, compact && styles.alertCompact, className]
    .filter(Boolean)
    .join(" ");
  return (
    <div role={toneName === "critical" ? "alert" : "status"} className={classes}>
      {icon && <Icon size={compact ? 14 : 16} className={styles.alertIcon} aria-hidden="true" />}
      <div className={styles.alertBody}>
        {title && <span className={styles.alertTitle}>{title}</span>}
        {children && <div>{children}</div>}
      </div>
    </div>
  );
}

// "Nothing here" — distinct from loading (use Skeleton for that), so an
// empty result never flashes while data is still on its way.
export function EmptyState({ icon: Icon, title, children, action, className }) {
  return (
    <div className={[styles.empty, className].filter(Boolean).join(" ")}>
      {Icon && <Icon size={28} strokeWidth={1.5} className={styles.emptyIcon} aria-hidden="true" />}
      {title && <p className={styles.emptyTitle}>{title}</p>}
      {children && <div className={styles.emptyBody}>{children}</div>}
      {action}
    </div>
  );
}

// Loading placeholder: `lines` shimmering bars (the last one shorter), or a
// single block when given a height. Announced once as "Loading…".
export function Skeleton({ lines = 3, height, width, label = "Loading…", className }) {
  if (height) {
    return (
      <div role="status" className={className}>
        <span className={styles.skeleton} style={{ height, width }} aria-hidden="true" />
        <span className="sr-only">{label}</span>
      </div>
    );
  }
  return (
    <div role="status" className={[styles.skeletonGroup, className].filter(Boolean).join(" ")}>
      {Array.from({ length: lines }, (_, i) => (
        <span
          key={i}
          className={styles.skeleton}
          style={{ width: i === lines - 1 && lines > 1 ? "60%" : width }}
          aria-hidden="true"
        />
      ))}
      <span className="sr-only">{label}</span>
    </div>
  );
}

// Label + big number (+ optional hint). `tone` tints the value.
export function Stat({ label, value, hint, tone: toneName, className }) {
  return (
    <div className={[styles.stat, toneName && tone[toneName], className].filter(Boolean).join(" ")}>
      <span className={styles.statLabel}>{label}</span>
      <span className={styles.statValue}>{value}</span>
      {hint && <span className={styles.statHint}>{hint}</span>}
    </div>
  );
}
