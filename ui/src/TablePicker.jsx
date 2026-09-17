import { useState } from "react";
import { formatMetricValue } from "./format";

export default function TablePicker({ tables, unit, selected, onToggle, onClear, maxSelected }) {
  const [search, setSearch] = useState("");

  const query = search.trim().toLowerCase();
  const filtered = query
    ? tables.filter((t) => `${t.schema_name}.${t.table_name}`.toLowerCase().includes(query))
    : tables;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {selected.size > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 6 }}>
          {[...selected].map((key) => (
            <span
              key={key}
              onClick={() => onToggle(key)}
              title="Remove from comparison"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "4px 10px",
                fontSize: 11.5,
                fontWeight: 650,
                color: "white",
                background: "var(--accent)",
                borderRadius: 100,
                cursor: "pointer",
                fontFamily: "IBM Plex Mono, monospace",
              }}
            >
              {key}
              <span style={{ fontSize: 13, lineHeight: 1 }}>&times;</span>
            </span>
          ))}
          <span onClick={onClear} style={{ fontSize: 11, color: "var(--text-muted)", cursor: "pointer" }}>
            Clear
          </span>
        </div>
      )}

      <input
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder={`Search ${tables.length} table${tables.length === 1 ? "" : "s"}…`}
      />

      <div style={{ maxHeight: 200, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 9 }}>
        {filtered.length === 0 && (
          <div style={{ padding: "10px 12px", fontSize: 12, color: "var(--text-muted)" }}>No matching tables.</div>
        )}
        {filtered.map((t, i) => {
          const key = `${t.schema_name}.${t.table_name}`;
          const active = selected.has(key);
          const disabled = !active && selected.size >= maxSelected;
          return (
            <div
              key={key}
              onClick={() => !disabled && onToggle(key)}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "6px 12px",
                fontSize: 12,
                cursor: disabled ? "default" : "pointer",
                background: active ? "var(--accent-tint)" : "transparent",
                opacity: disabled ? 0.45 : 1,
                borderBottom: i === filtered.length - 1 ? "none" : "1px solid var(--border)",
              }}
            >
              <span style={{ fontFamily: "IBM Plex Mono, monospace", color: "var(--text)" }}>{key}</span>
              <span style={{ fontFamily: "IBM Plex Mono, monospace", color: "var(--text-muted)" }}>
                {formatMetricValue(t.latest_value, unit)}
              </span>
            </div>
          );
        })}
      </div>

      <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Compare up to {maxSelected} tables at once.</span>
    </div>
  );
}
