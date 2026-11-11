import { useId, useState } from "react";
import { ChevronRight } from "lucide-react";
import styles from "./CollapsibleGroup.module.css";

// A bordered group with a disclosure header (Advisor's finding groups, Table
// Health's per-table findings, Connections' optional PgBouncer section).
// The header is a real button wired to the region it shows/hides; `count`
// is optional and rendered as a pill on the right when given.
export default function CollapsibleGroup({ label, count, defaultExpanded = false, children }) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const regionId = useId();
  return (
    <div className={styles.group}>
      <button
        type="button"
        className={styles.header}
        aria-expanded={expanded}
        aria-controls={expanded ? regionId : undefined}
        onClick={() => setExpanded((prev) => !prev)}
      >
        <span className={styles.title}>
          <ChevronRight size={14} className={styles.chevron} aria-hidden="true" />
          <span className={styles.label}>{label}</span>
        </span>
        {count != null && <span className={styles.count}>{count}</span>}
      </button>
      {expanded && (
        <div id={regionId} className={styles.body}>
          {children}
        </div>
      )}
    </div>
  );
}
