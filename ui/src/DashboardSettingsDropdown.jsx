import { useEffect, useRef, useState } from "react";

// Same dropdown-trigger pattern as CategoryFilterDropdown, but keyed by
// {key, label, description} objects rather than flat strings — the
// Dashboard's toggleable identity (CategoryStatus.key / widget key) and its
// display label aren't the same string here, and each item gets a
// description too.
export default function DashboardSettingsDropdown({ categories, hidden, onToggle }) {
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

  return (
    <div ref={rootRef} style={{ position: "relative" }}>
      <button
        className="button-secondary"
        title="Choose which tiles and widgets show on the Dashboard"
        onClick={() => setOpen((o) => !o)}
        style={{ padding: "5px 11px", fontSize: 11.5, display: "flex", alignItems: "center", gap: 6 }}
      >
        <span>⚙</span> Customize
        {hidden.size > 0 && <span style={{ fontSize: 10, color: "var(--text-muted)" }}>({hidden.size} hidden)</span>}
      </button>

      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            right: 0,
            zIndex: 1000,
            minWidth: 300,
            maxHeight: 380,
            overflowY: "auto",
            padding: 10,
            background: "var(--card)",
            border: "1px solid var(--border)",
            borderRadius: 12,
            boxShadow: "0 8px 24px oklch(0% 0 0 / 15%)",
            display: "flex",
            flexDirection: "column",
            gap: 2,
          }}
        >
          <span
            style={{
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: "0.02em",
              textTransform: "uppercase",
              color: "var(--text-muted)",
              padding: "2px 4px 8px",
            }}
          >
            Dashboard tiles &amp; widgets
          </span>
          {categories.map((cat) => (
            <label
              key={cat.key}
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 8,
                padding: "6px 6px",
                borderRadius: 6,
                cursor: "pointer",
              }}
            >
              <input
                type="checkbox"
                checked={!hidden.has(cat.key)}
                onChange={() => onToggle(cat.key)}
                style={{ width: "auto", flexShrink: 0, marginTop: 2, padding: 0, border: "none", background: "none" }}
              />
              <span style={{ display: "flex", flexDirection: "column" }}>
                <span style={{ fontFamily: "Manrope, sans-serif", fontWeight: 600, fontSize: 12.5, color: "var(--text)" }}>
                  {cat.label}
                </span>
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{cat.description}</span>
              </span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
