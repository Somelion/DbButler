export default function ScreenHeader({ title, subtitle, mode, onModeChange, targetSwitcher }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
        <span
          style={{
            fontFamily: "Manrope, sans-serif",
            fontWeight: 700,
            fontSize: 22,
            letterSpacing: "-0.01em",
            color: "var(--text)",
          }}
        >
          {title}
        </span>
        {subtitle && (
          <span style={{ fontFamily: "IBM Plex Mono, monospace", fontSize: 12.5, color: "var(--text-muted)" }}>
            {subtitle}
          </span>
        )}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
        {onModeChange && (
          <div style={{ display: "flex", alignItems: "center", padding: 3, background: "var(--row-bg)", borderRadius: 9 }}>
            {["simple", "advanced"].map((m) => (
              <span
                key={m}
                onClick={() => onModeChange(m)}
                style={{
                  padding: "6px 14px",
                  fontSize: 12.5,
                  fontWeight: mode === m ? 650 : 600,
                  color: mode === m ? "white" : "var(--text-secondary)",
                  background: mode === m ? "var(--accent)" : "transparent",
                  borderRadius: 7,
                  cursor: "pointer",
                  textTransform: "capitalize",
                }}
              >
                {m}
              </span>
            ))}
          </div>
        )}

        {targetSwitcher}
      </div>
    </div>
  );
}
