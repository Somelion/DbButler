import { useTranslation } from "react-i18next";
import { Settings2 } from "lucide-react";
import styles from "./CategoryFilterDropdown.module.css";
import { Checkbox, Popover, popoverStyles } from "./components";

// Show/hide filter: a Popover with one checkbox per item plus Select all /
// Select none. Generic over "items" so it can drive both the Table Health
// schema filter and the Advisor tab/category/schema filters from the same
// component. A checked item is shown; `hiddenItems` holds the unchecked ones.
// `itemLabel` optionally maps an item to its displayed text (e.g. a
// translation), so the item itself can stay a stable stored id.
export default function CategoryFilterDropdown({
  label,
  items,
  hiddenItems,
  onToggle,
  onSelectAll,
  onSelectNone,
  itemLabel = (item) => item,
}) {
  const { t } = useTranslation("findings");
  if (items.length === 0) return null;

  const ariaLabel =
    hiddenItems.size > 0
      ? t("filter.ariaLabelHidden", { label, lowerLabel: label.toLowerCase(), count: hiddenItems.size })
      : t("filter.ariaLabel", { label, lowerLabel: label.toLowerCase() });

  return (
    <Popover
      ariaLabel={ariaLabel}
      panelLabel={label}
      buttonClassName={`button-secondary button-sm ${styles.trigger}`}
      label={
        <>
          <Settings2 size={13} aria-hidden="true" />
          {label}
          {hiddenItems.size > 0 && (
            <span className={styles.hiddenCount}>{t("filter.hiddenCount", { count: hiddenItems.size })}</span>
          )}
        </>
      }
    >
      {() => (
        <>
          <div className={styles.bulkActions}>
            <button type="button" className="button-link" onClick={onSelectAll}>
              {t("common:actions.selectAll")}
            </button>
            <button type="button" className="button-link" onClick={onSelectNone}>
              {t("common:actions.selectNone")}
            </button>
          </div>
          <div className={popoverStyles.divider} />

          {items.map((item) => (
            <Checkbox
              key={item}
              checked={!hiddenItems.has(item)}
              onChange={() => onToggle(item)}
              className={`${popoverStyles.option} ${styles.option}`}
            >
              <span className={styles.itemLabel}>{itemLabel(item)}</span>
            </Checkbox>
          ))}
        </>
      )}
    </Popover>
  );
}
