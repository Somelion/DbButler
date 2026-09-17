export default function QueryParamsForm({ placeholders, values, onChange }) {
  if (placeholders.length === 0) return null;

  const inputStyle = {
    width: 130,
    padding: "5px 8px",
    borderRadius: 6,
    border: "1px solid var(--border)",
    background: "var(--bg)",
    color: "var(--text)",
    fontFamily: "IBM Plex Mono, monospace",
    fontSize: 12,
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        padding: "10px 12px",
        background: "var(--accent-tint)",
        borderRadius: 9,
      }}
    >
      <span style={{ fontSize: 11.5, color: "oklch(35% 0.1 255)", lineHeight: 1.5 }}>
        This query has parameter placeholders — pg_stat_statements replaces literal values with these before
        storing the query. Fill in a value for each one exactly as you'd write it in SQL (e.g. <code>42</code>,{" "}
        <code>'active'</code>, <code>now()</code>) before running it.
      </span>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
        {placeholders.map((n) => (
          <label key={n} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text)" }}>
            ${n}
            <input
              type="text"
              value={values[n] ?? ""}
              onChange={(e) => onChange(n, e.target.value)}
              style={inputStyle}
            />
          </label>
        ))}
      </div>
    </div>
  );
}
