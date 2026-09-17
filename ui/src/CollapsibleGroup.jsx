import { useState } from "react";

export default function CollapsibleGroup({ label, count, defaultExpanded = false, children }) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        border: "1px solid var(--border)",
        borderRadius: 12,
        overflow: "hidden",
      }}
    >
      <div
        onClick={() => setExpanded((prev) => !prev)}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "10px 14px",
          background: "var(--row-bg)",
          cursor: "pointer",
          userSelect: "none",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            style={{
              fontSize: 10,
              color: "var(--text-muted)",
              display: "inline-block",
              transform: expanded ? "rotate(90deg)" : "rotate(0deg)",
              transition: "transform 0.12s ease",
            }}
          >
            ▶
          </span>
          <span style={{ fontFamily: "Manrope, sans-serif", fontWeight: 650, fontSize: 13.5, color: "var(--text)" }}>
            {label}
          </span>
        </div>
        <span
          style={{
            fontSize: 11.5,
            fontWeight: 700,
            color: "var(--text-muted)",
            background: "var(--card)",
            padding: "2px 9px",
            borderRadius: 100,
          }}
        >
          {count}
        </span>
      </div>
      {expanded && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: "12px 14px" }}>{children}</div>
      )}
    </div>
  );
}
