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
export default function TargetSwitcher({ targets, activeTargetId, onSelect, onManage }) {
  const { t } = useTranslation();
  if (targets.length === 0) return null;

  const active = targets.find((target) => target.id === activeTargetId) ?? null;
  const statusLabel = (target) => (target?.last_test_ok ? t("nav.connected") : t("nav.notConnected"));

  return (
    <Popover
      ariaLabel={t("switcher.ariaLabel", { name: active ? active.name : t("switcher.noneSelected") })}
      panelLabel={t("switcher.databases")}
      buttonClassName={styles.trigger}
      label={
        <>
          <StatusDot tone={active?.last_test_ok ? "healthy" : "unknown"} label={statusLabel(active)} hideLabel />
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
              <StatusDot tone={target.last_test_ok ? "healthy" : "unknown"} label={statusLabel(target)} hideLabel />
              <span className={styles.optionText}>
                <span className={styles.optionName}>{target.name}</span>
                <span className={styles.optionAddress}>
                  {target.host}:{target.port}/{target.dbname}
                  {!target.is_active && ` · ${t("switcher.paused")}`}
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
