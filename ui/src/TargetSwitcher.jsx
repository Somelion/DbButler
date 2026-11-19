import { useTranslation } from "react-i18next";
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
// The status dot: grey while paused (not watched, whatever its last status),
// red when the last connect failed — a manual test or the background jobs,
// which keep last_test_ok current — and green while it's being monitored.
function targetStatus(target) {
  if (!target) return { tone: "neutral", label: "switcher.select" };
  if (!target.is_active) return { tone: "neutral", label: "switcher.statusPaused" };
  if (target.last_test_ok === false) return { tone: "critical", label: "switcher.statusProblem" };
  return { tone: "healthy", label: "switcher.statusMonitored" };
}

function TargetStatusDot({ target, className }) {
  const { t } = useTranslation();
  const status = targetStatus(target);
  return <StatusDot tone={status.tone} label={t(status.label)} hideLabel className={className} />;
}

export default function TargetSwitcher({ targets, activeTargetId, onSelect, onManage }) {
  const { t } = useTranslation();
  if (targets.length === 0) return null;

  const active = targets.find((target) => target.id === activeTargetId) ?? null;

  return (
    <Popover
      ariaLabel={t("switcher.ariaLabel", {
        name: active ? active.name : t("switcher.noneSelected"),
        status: t(targetStatus(active).label),
      })}
      panelLabel={t("switcher.databases")}
      buttonClassName={styles.trigger}
      label={
        <>
          <TargetStatusDot target={active} className={styles.statusDot} />
          <span className={styles.triggerName}>{active ? active.name : t("switcher.select")}</span>
          <ChevronDown size={14} className={styles.chevron} aria-hidden="true" />
        </>
      }
      panelClassName={styles.panel}
    >
      {({ close }) => (
        <>
          {targets.map((target) => (
            <button
              key={target.id}
              type="button"
              className={popoverStyles.option}
              aria-current={target.id === activeTargetId ? "true" : undefined}
              onClick={() => {
                close();
                onSelect(target.id);
              }}
            >
              <TargetStatusDot target={target} className={styles.statusDot} />
              <span className={styles.optionText}>
                <span className={styles.optionName}>{target.name}</span>
                <span className={styles.optionAddress}>
                  {target.host}:{target.port}/{target.dbname}
                  {!target.is_active
                    ? ` · ${t("switcher.paused")}`
                    : target.last_test_ok === false && ` · ${t("switcher.problem")}`}
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
            {t("switcher.manage")}
          </button>
        </>
      )}
    </Popover>
  );
}
