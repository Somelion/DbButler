import { useTranslation } from "react-i18next";
import { Settings2 } from "lucide-react";
import { Checkbox, Popover, popoverStyles } from "./components";
import styles from "./DashboardSettingsDropdown.module.css";

// Same dropdown-trigger pattern as CategoryFilterDropdown (the shared
// Popover), but keyed by {key, label, description} objects rather than flat
// strings — the Dashboard's toggleable identity (CategoryStatus.key / widget
// key) and its display label aren't the same string here, and each item
// gets a description too.
export default function DashboardSettingsDropdown({ categories, hidden, onToggle }) {
  const { t } = useTranslation("dashboard");
  return (
    <Popover
      buttonClassName="button-secondary button-sm"
      panelClassName={styles.panel}
      panelLabel={t("settings.panelLabel")}
      label={
        <>
          <Settings2 size={13} aria-hidden="true" />
          {t("settings.customize")}
          {hidden.size > 0 && <span className={styles.hiddenCount}>{t("settings.hiddenCount", { n: hidden.size })}</span>}
        </>
      }
    >
      {() => (
        <>
          <span className={popoverStyles.sectionLabel}>{t("settings.sectionLabel")}</span>
          {categories.map((cat) => (
            <Checkbox
              key={cat.key}
              checked={!hidden.has(cat.key)}
              onChange={() => onToggle(cat.key)}
              className={`${popoverStyles.option} ${styles.option}`}
            >
              <span className={styles.optionText}>
                <span className={styles.optionLabel}>{cat.label}</span>
                <span className={styles.optionDescription}>{cat.description}</span>
              </span>
            </Checkbox>
          ))}
        </>
      )}
    </Popover>
  );
}
