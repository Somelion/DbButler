import { useState } from "react";

// Modeled on TablePicker.jsx's search + checkbox-list interaction, but
// shaped for queries: a checkbox adds/removes a query from the comparison
// overlay (capped at maxSelected), and a separate "Deep-dive" button per row
// sets it as the single focused query for the deep-dive section — the two
// selection modes read from the same list rather than showing it twice.
export default function QueryPicker({ queries, selected, onToggle, onClear, maxSelected, focusedQueryId, onFocus }) {
  const [search, setSearch] = useState("");

  const term = search.trim().toLowerCase();
  const filtered = term ? queries.filter((q) => q.query.toLowerCase().includes(term)) : queries;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {selected.size > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 6 }}>
          {[...selected].map((id) => {
            const q = queries.find((qq) => qq.queryid === id);
            return (
              <span
                key={id}
                onClick={() => onToggle(id)}
                title={q ? `Remove from comparison: ${q.query}` : "Remove from comparison"}
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
                  maxWidth: 220,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {q ? q.query : id}
                <span style={{ fontSize: 13, lineHeight: 1, flexShrink: 0 }}>&times;</span>
              </span>
            );
          })}
          <span onClick={onClear} style={{ fontSize: 11, color: "var(--text-muted)", cursor: "pointer" }}>
            Clear
          </span>
        </div>
      )}

      <input
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder={`Search ${queries.length} tracked quer${queries.length === 1 ? "y" : "ies"}…`}
      />

      <div style={{ maxHeight: 280, overflowY: "auto", overflowX: "auto", border: "1px solid var(--border)", borderRadius: 9 }}>
        {filtered.length === 0 && (
          <div style={{ padding: "10px 12px", fontSize: 12, color: "var(--text-muted)" }}>No matching queries.</div>
        )}
        {filtered.map((q, i) => {
          const active = selected.has(q.queryid);
          const disabled = !active && selected.size >= maxSelected;
          const isFocused = focusedQueryId === q.queryid;
          return (
            <div
              key={q.queryid}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "6px 12px",
                fontSize: 12,
                background: isFocused ? "var(--attention-bg)" : active ? "var(--accent-tint)" : "transparent",
                borderBottom: i === filtered.length - 1 ? "none" : "1px solid var(--border)",
              }}
            >
              <input
                type="checkbox"
                checked={active}
                disabled={disabled}
                onChange={() => onToggle(q.queryid)}
                style={{ flexShrink: 0, cursor: disabled ? "default" : "pointer" }}
              />
              <span
                title={q.query}
                style={{
                  flex: "1 1 auto",
                  // A flex item with minWidth:0 is willing to shrink all the
                  // way to nothing once its siblings' own content (checkbox,
                  // call count, button — none of which opt into shrinking
                  // below their own size) claims the rest of a narrow row,
                  // which silently clips 100% of the query text instead of
                  // just some of it. A real pixel floor keeps this column
                  // legible — the row scrolls horizontally (see the list
                  // container's overflowX) rather than collapsing the one
                  // column that actually matters.
                  minWidth: 160,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  fontFamily: "IBM Plex Mono, monospace",
                  color: "var(--text)",
                }}
              >
                {q.query}
              </span>
              <span style={{ fontFamily: "IBM Plex Mono, monospace", color: "var(--text-muted)", flexShrink: 0 }}>
                {q.calls.toLocaleString()} calls
              </span>
              <button
                className={isFocused ? "button-primary" : "button-secondary"}
                onClick={() => onFocus(q.queryid)}
                style={{ padding: "3px 9px", fontSize: 10.5, flexShrink: 0 }}
              >
                {isFocused ? "Deep-diving" : "Deep-dive"}
              </button>
            </div>
          );
        })}
      </div>

      <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Compare up to {maxSelected} queries at once.</span>
    </div>
  );
}
