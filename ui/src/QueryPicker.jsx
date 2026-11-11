import { useId, useState } from "react";
import { X } from "lucide-react";
import { Checkbox } from "./components";
import styles from "./QueryPicker.module.css";

// Modeled on TablePicker.jsx's search + checkbox-list interaction, but
// shaped for queries: a checkbox adds/removes a query from the comparison
// overlay (capped at maxSelected), and a separate "Deep-dive" toggle button
// per row sets it as the single focused query for the deep-dive section —
// the two selection modes read from the same list rather than showing it
// twice.
export default function QueryPicker({ queries, selected, onToggle, onClear, maxSelected, focusedQueryId, onFocus }) {
  const [search, setSearch] = useState("");
  const idPrefix = useId();

  const term = search.trim().toLowerCase();
  const filtered = term ? queries.filter((q) => q.query.toLowerCase().includes(term)) : queries;

  return (
    <div className={styles.picker}>
      {selected.size > 0 && (
        <div className={styles.chips}>
          {[...selected].map((id) => {
            const q = queries.find((qq) => qq.queryid === id);
            const label = q ? q.query : String(id);
            return (
              <button
                key={id}
                type="button"
                className={styles.chip}
                onClick={() => onToggle(id)}
                title={`Remove from comparison: ${label}`}
                aria-label={`Remove from comparison: ${label}`}
              >
                <span className={styles.chipText}>{label}</span>
                <X size={13} className={styles.chipIcon} aria-hidden="true" />
              </button>
            );
          })}
          <button type="button" className="button-link" onClick={onClear}>
            Clear
          </button>
        </div>
      )}

      <input
        type="search"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        aria-label="Search tracked queries"
        placeholder={`Search ${queries.length} tracked quer${queries.length === 1 ? "y" : "ies"}…`}
      />

      {filtered.length === 0 ? (
        <p className={styles.noMatch}>No matching queries.</p>
      ) : (
        <ul className={styles.list} aria-label="Tracked queries">
          {filtered.map((q) => {
            const active = selected.has(q.queryid);
            const disabled = !active && selected.size >= maxSelected;
            const isFocused = focusedQueryId === q.queryid;
            const textId = `${idPrefix}-${q.queryid}`;
            const rowClass = [styles.row, active && styles.rowSelected, isFocused && styles.rowFocused]
              .filter(Boolean)
              .join(" ");
            return (
              <li key={q.queryid} className={rowClass}>
                <Checkbox
                  className={styles.rowCheck}
                  checked={active}
                  disabled={disabled}
                  onChange={() => onToggle(q.queryid)}
                >
                  <span id={textId} title={q.query} className={styles.queryText}>
                    {q.query}
                  </span>
                </Checkbox>
                <span className={styles.calls}>{q.calls.toLocaleString()} calls</span>
                <button
                  type="button"
                  className={`${isFocused ? "button-primary" : "button-secondary"} button-sm ${styles.deepDive}`}
                  aria-pressed={isFocused}
                  aria-describedby={textId}
                  onClick={() => onFocus(q.queryid)}
                >
                  {isFocused ? "Deep-diving" : "Deep-dive"}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <p className={styles.hint}>Compare up to {maxSelected} queries at once.</p>
    </div>
  );
}
