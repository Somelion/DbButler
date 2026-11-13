import { useState } from "react";
import { useTranslation } from "react-i18next";
import { X } from "lucide-react";
import styles from "./TablePicker.module.css";
import { formatMetricValue } from "./format";

// Search + checkbox list for picking which tables to compare. Selected
// tables also show as removable chips above the search box. Once
// `maxSelected` is reached, the unchecked rows are disabled.
export default function TablePicker({ tables, unit, selected, onToggle, onClear, maxSelected }) {
  const { t } = useTranslation("trends");
  const [search, setSearch] = useState("");

  const query = search.trim().toLowerCase();
  const filtered = query
    ? tables.filter((table) => `${table.schema_name}.${table.table_name}`.toLowerCase().includes(query))
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
              aria-label={t("picker.removeAria", { table: key })}
              title={t("picker.removeTitle")}
            >
              {key}
              <X size={12} aria-hidden="true" />
            </button>
          ))}
          <button type="button" className={`button-link ${styles.clear}`} onClick={onClear}>
            {t("picker.clear")}
          </button>
        </div>
      )}

      <input
        type="search"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder={t("picker.search", { count: tables.length })}
        aria-label={t("picker.searchAria")}
      />

      <div className={styles.list} role="group" aria-label={t("picker.listAria")}>
        {filtered.length === 0 && <div className={styles.noMatch}>{t("picker.noMatch")}</div>}
        {filtered.map((table) => {
          const key = `${table.schema_name}.${table.table_name}`;
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
              <span className={styles.value}>{formatMetricValue(table.latest_value, unit)}</span>
            </label>
          );
        })}
      </div>

      <span className={styles.hint}>{t("picker.compareUpTo", { count: maxSelected })}</span>
    </div>
  );
}
