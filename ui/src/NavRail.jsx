const NAV_ITEMS = [
  { key: "dashboard", label: "Dashboard" },
  { key: "connections", label: "Connections" },
  { key: "activity", label: "Activity" },
  { key: "table-health", label: "Table Health" },
  { key: "query-intelligence", label: "Query Intelligence" },
  { key: "query-history", label: "Query History" },
  { key: "plan-regressions", label: "Plan Regressions" },
  { key: "ai-analysis", label: "AI Analysis" },
  { key: "advisor", label: "Advisor" },
  { key: "index-testing", label: "Index Testing" },
  { key: "archive", label: "Archive" },
  { key: "config-tuning", label: "Config Tuning" },
  { key: "trends", label: "Trends" },
  { key: "settings", label: "Settings" },
];

export default function NavRail({ active, onSelect, connected, badges = {} }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        width: 212,
        flexShrink: 0,
        height: "100%",
        padding: "22px 14px",
        background: "var(--row-bg)",
        borderRight: "1px solid var(--border)",
        boxSizing: "border-box",
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "0 8px" }}>
          <svg
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="none"
            stroke="var(--accent)"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <ellipse cx="12" cy="5" rx="8" ry="3" />
            <path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5" />
            <path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6" />
          </svg>
          <span
            style={{
              fontFamily: "Manrope, sans-serif",
              fontWeight: 700,
              fontSize: 15.5,
              color: "var(--text)",
              letterSpacing: "-0.01em",
            }}
          >
            PostgreDba
          </span>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {NAV_ITEMS.map((item) => {
            const disabled = item.comingSoon || (!connected && item.key !== "connections");
            const isActive = active === item.key;
            return (
              <div
                key={item.key}
                onClick={() => !disabled && onSelect(item.key)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 10,
                  padding: "9px 12px",
                  borderRadius: 9,
                  background: isActive ? "var(--accent-tint)" : "transparent",
                  cursor: disabled ? "default" : "pointer",
                  opacity: disabled ? 0.45 : 1,
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: "50%",
                      background: isActive ? "var(--accent)" : "var(--border)",
                      flexShrink: 0,
                    }}
                  />
                  <span
                    style={{
                      fontSize: 13.5,
                      fontWeight: isActive ? 650 : 500,
                      color: isActive ? "var(--accent)" : "var(--text)",
                    }}
                  >
                    {item.label}
                  </span>
                </div>
                {item.comingSoon && (
                  <span style={{ fontSize: 9.5, fontWeight: 700, color: "var(--text-muted)" }}>SOON</span>
                )}
                {!item.comingSoon && badges[item.key] > 0 && (
                  <span
                    style={{
                      fontSize: 10.5,
                      fontWeight: 700,
                      color: "white",
                      background: "var(--accent)",
                      borderRadius: 100,
                      minWidth: 16,
                      height: 16,
                      lineHeight: "16px",
                      textAlign: "center",
                      padding: "0 4px",
                    }}
                  >
                    {badges[item.key]}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 9,
          padding: "10px 8px",
          borderTop: "1px solid var(--border)",
        }}
      >
        <span
          style={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            background: connected ? "var(--healthy-text)" : "var(--text-muted)",
            flexShrink: 0,
          }}
        />
        <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>local instance</span>
      </div>
    </div>
  );
}
