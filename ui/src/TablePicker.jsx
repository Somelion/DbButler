import { useState } from "react";
import { X } from "lucide-react";
import styles from "./TablePicker.module.css";
import { formatMetricValue } from "./format";

// Search + checkbox list for picking which tables to compare. Selected
// tables also show as removable chips above the search box. Once
// `maxSelected` is reached, the unchecked rows are disabled.
export default function TablePicker({ tables, unit, selected, onToggle, onClear, maxSelected }) {
  const [search, setSearch] = useState("");

  const query = search.trim().toLowerCase();
  const filtered = query
    ? tables.filter((t) => `${t.schema_name}.${t.table_name}`.toLowerCase().includes(query))
    : tables;

  return (
    <div className={styles.picker}>
      {selected.size > 0 && (
        <div className={styles.chips}>
          {[...selected].map((key) => (
            <button
              key={key}
              type="button"
              className={styles.chip}
              onClick={() => onToggle(key)}
              aria-label={`Remove ${key} from comparison`}
              title="Remove from comparison"
            >
              {key}
              <X size={12} aria-hidden="true" />
            </button>
          ))}
          <button type="button" className={`button-link ${styles.clear}`} onClick={onClear}>
            Clear
          </button>
        </div>
      )}

      <input
        type="search"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder={`Search ${tables.length} table${tables.length === 1 ? "" : "s"}…`}
        aria-label="Search tables"
      />

      <div className={styles.list} role="group" aria-label="Tables to compare">
        {filtered.length === 0 && <div className={styles.noMatch}>No matching tables.</div>}
        {filtered.map((t) => {
          const key = `${t.schema_name}.${t.table_name}`;
          const active = selected.has(key);
          const disabled = !active && selected.size >= maxSelected;
          return (
            <label
              key={key}
              className={[styles.row, active && styles.rowActive, disabled && styles.rowDisabled].filter(Boolean).join(" ")}
            >
              <input
                type="checkbox"
                className={styles.checkbox}
                checked={active}
                disabled={disabled}
                onChange={() => onToggle(key)}
              />
              <span className={styles.tableName}>{key}</span>
              <span className={styles.value}>{formatMetricValue(t.latest_value, unit)}</span>
            </label>
          );
        })}
      </div>

      <span className={styles.hint}>Compare up to {maxSelected} tables at once.</span>
    </div>
  );
}
