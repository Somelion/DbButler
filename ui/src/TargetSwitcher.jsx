import { useEffect, useRef, useState } from "react";

// Lives in ScreenHeader's right-aligned slot, next to the Simple/Advanced
// mode toggle — a viewport switch between saved connections, not a
// monitoring on/off switch (every is_active target keeps being collected/
// scanned/alerted-on in the background regardless of which one is
// currently displayed). Deliberately minimal: switch or add a connection
// here; rename/pause/remove stay on the Connections screen rather than
// duplicating management actions into a quick-access dropdown.
export default function TargetSwitcher({ targets, activeTargetId, onSelect, onManage }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const handleClickOutside = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [open]);

  if (targets.length === 0) return null;

  const active = targets.find((t) => t.id === activeTargetId) ?? null;

  return (
    <div ref={rootRef} style={{ position: "relative" }}>
      <div
        onClick={() => setOpen((o) => !o)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "6px 12px",
          background: "var(--row-bg)",
          border: "1px solid var(--border)",
          borderRadius: 9,
          cursor: "pointer",
          maxWidth: 220,
        }}
      >
        <span
          style={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            background: active?.last_test_ok ? "var(--healthy-text)" : "var(--text-muted)",
            flexShrink: 0,
          }}
        />
        <span
          style={{
            fontSize: 12.5,
            fontWeight: 600,
            color: "var(--text)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {active ? active.name : "Select a database"}
        </span>
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>▾</span>
      </div>

      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            right: 0,
            zIndex: 1000,
            minWidth: 260,
            padding: 6,
            background: "var(--card)",
            border: "1px solid var(--border)",
            borderRadius: 12,
            boxShadow: "0 8px 24px oklch(0% 0 0 / 15%)",
            display: "flex",
            flexDirection: "column",
            gap: 2,
          }}
        >
          {targets.map((t) => (
            <div
              key={t.id}
              onClick={() => {
                onSelect(t.id);
                setOpen(false);
              }}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "8px 10px",
                borderRadius: 8,
                cursor: "pointer",
                background: t.id === activeTargetId ? "var(--accent-tint)" : "transparent",
              }}
            >
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: "50%",
                  background: t.last_test_ok ? "var(--healthy-text)" : "var(--text-muted)",
                  flexShrink: 0,
                }}
              />
              <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
                <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text)" }}>{t.name}</span>
                <span
                  style={{
                    fontFamily: "IBM Plex Mono, monospace",
                    fontSize: 10.5,
                    color: "var(--text-muted)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {t.host}:{t.port}/{t.dbname}
                  {!t.is_active && " · paused"}
                </span>
              </div>
            </div>
          ))}

          <div style={{ height: 1, background: "var(--border)", margin: "4px 0" }} />

          <div
            onClick={() => {
              onManage();
              setOpen(false);
            }}
            style={{
              padding: "8px 10px",
              borderRadius: 8,
              cursor: "pointer",
              fontSize: 12.5,
              fontWeight: 600,
              color: "var(--accent)",
            }}
          >
            + Add / manage connections
          </div>
        </div>
      )}
    </div>
  );
}
