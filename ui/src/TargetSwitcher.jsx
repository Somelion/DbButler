import { ChevronDown, Plus } from "lucide-react";
import styles from "./TargetSwitcher.module.css";
import { Popover, StatusDot, popoverStyles } from "./components";

// Lives in ScreenHeader's right-aligned slot, next to the Simple/Advanced
// mode toggle — a viewport switch between saved connections, not a
// monitoring on/off switch (every is_active target keeps being collected/
// scanned/alerted-on in the background regardless of which one is
// currently displayed). Deliberately minimal: switch or add a connection
// here; rename/pause/remove stay on the Connections screen rather than
// duplicating management actions into a quick-access dropdown.
export default function TargetSwitcher({ targets, activeTargetId, onSelect, onManage }) {
  if (targets.length === 0) return null;

  const active = targets.find((t) => t.id === activeTargetId) ?? null;

  return (
    <Popover
      ariaLabel={`Database: ${active ? active.name : "none selected"}. Switch database`}
      panelLabel="Databases"
      buttonClassName={styles.trigger}
      label={
        <>
          <StatusDot
            tone={active?.last_test_ok ? "healthy" : "unknown"}
            label={active?.last_test_ok ? "Connected" : "Not connected"}
            hideLabel
          />
          <span className={styles.triggerName}>{active ? active.name : "Select a database"}</span>
          <ChevronDown size={14} className={styles.chevron} aria-hidden="true" />
        </>
      }
      panelClassName={styles.panel}
    >
      {({ close }) => (
        <>
          {targets.map((t) => (
            <button
              key={t.id}
              type="button"
              className={popoverStyles.option}
              aria-current={t.id === activeTargetId ? "true" : undefined}
              onClick={() => {
                close();
                onSelect(t.id);
              }}
            >
              <StatusDot
                tone={t.last_test_ok ? "healthy" : "unknown"}
                label={t.last_test_ok ? "Connected" : "Not connected"}
                hideLabel
              />
              <span className={styles.optionText}>
                <span className={styles.optionName}>{t.name}</span>
                <span className={styles.optionAddress}>
                  {t.host}:{t.port}/{t.dbname}
                  {!t.is_active && " · paused"}
                </span>
              </span>
            </button>
          ))}

          <div className={popoverStyles.divider} />

          <button
            type="button"
            className={`${popoverStyles.option} ${styles.manage}`}
            onClick={() => {
              close();
              onManage();
            }}
          >
            <Plus size={14} aria-hidden="true" />
            Add / manage connections
          </button>
        </>
      )}
    </Popover>
  );
}
