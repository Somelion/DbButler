import { Settings2 } from "lucide-react";
import styles from "./CategoryFilterDropdown.module.css";
import { Checkbox, Popover, popoverStyles } from "./components";

// Show/hide filter: a Popover with one checkbox per item plus Select all /
// Select none. Generic over "items" so it can drive both the Table Health
// schema filter and the Advisor tab/category/schema filters from the same
// component. A checked item is shown; `hiddenItems` holds the unchecked ones.
export default function CategoryFilterDropdown({ label, items, hiddenItems, onToggle, onSelectAll, onSelectNone }) {
  if (items.length === 0) return null;

  const hiddenText = hiddenItems.size > 0 ? ` (${hiddenItems.size} hidden)` : "";

  return (
    <Popover
      ariaLabel={`${label}${hiddenText}: choose which ${label.toLowerCase()} to show`}
      panelLabel={label}
      buttonClassName={`button-secondary button-sm ${styles.trigger}`}
      label={
        <>
          <Settings2 size={13} aria-hidden="true" />
          {label}
          {hiddenItems.size > 0 && <span className={styles.hiddenCount}>({hiddenItems.size} hidden)</span>}
        </>
      }
    >
      {() => (
        <>
          <div className={styles.bulkActions}>
            <button type="button" className="button-link" onClick={onSelectAll}>
              Select all
            </button>
            <button type="button" className="button-link" onClick={onSelectNone}>
              Select none
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
              <span className={styles.itemLabel}>{item}</span>
            </Checkbox>
          ))}
        </>
      )}
    </Popover>
  );
}
