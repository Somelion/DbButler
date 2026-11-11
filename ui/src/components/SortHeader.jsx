import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import styles from "./Table.module.css";

// Sortable <th>: a real button inside, plus aria-sort on the header cell so
// screen readers announce the current order.
//
//   <SortHeader column="dead_pct" sortKey={sortKey} sortDir={sortDir}
//     onSort={handleSort} hint={<HeaderHint text="…" />}>Dead %</SortHeader>
//
// `hint` renders next to the button, not inside it, so it may be anything.
export default function SortHeader({ column, sortKey, sortDir, onSort, numeric = false, hint, className, children }) {
  const active = sortKey === column;
  const ariaSort = active ? (sortDir === "asc" ? "ascending" : "descending") : "none";
  const Icon = active ? (sortDir === "asc" ? ArrowUp : ArrowDown) : ArrowUpDown;

  return (
    <th
      scope="col"
      aria-sort={ariaSort}
      className={[styles.th, numeric && styles.numeric, className].filter(Boolean).join(" ")}
    >
      <button type="button" className={styles.sortButton} onClick={() => onSort(column)}>
        {children}
        <Icon size={11} className={styles.sortIcon} aria-hidden="true" />
      </button>
      {hint}
    </th>
  );
}
