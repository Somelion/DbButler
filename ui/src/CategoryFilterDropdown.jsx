import { useEffect, useRef, useState } from "react";

// Dropdown-trigger pattern borrowed from TargetSwitcher: a clickable trigger
// that toggles an absolutely-positioned panel, closed by an outside click.
// Generic over "items" so it can drive both the Table Health schema filter
// and the Advisor category filter from the same component.
export default function CategoryFilterDropdown({ label, items, hiddenItems, onToggle, onSelectAll, onSelectNone }) {
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

  if (items.length === 0) return null;

  return (
    <div ref={rootRef} style={{ position: "relative" }}>
      <button
        className="button-secondary"
        title={`Choose which ${label.toLowerCase()} to show`}
        onClick={() => setOpen((o) => !o)}
        style={{ padding: "4px 10px", fontSize: 11, display: "flex", alignItems: "center", gap: 6 }}
      >
        <span>⚙</span> {label}
        {hiddenItems.size > 0 && (
          <span style={{ fontSize: 10, color: "var(--text-muted)" }}>({hiddenItems.size} hidden)</span>
        )}
      </button>

      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            right: 0,
            zIndex: 1000,
            minWidth: 220,
            maxHeight: 320,
            overflowY: "auto",
            padding: 8,
            background: "var(--card)",
            border: "1px solid var(--border)",
            borderRadius: 12,
            boxShadow: "0 8px 24px oklch(0% 0 0 / 15%)",
            display: "flex",
            flexDirection: "column",
            gap: 2,
          }}
        >
          <div style={{ display: "flex", gap: 8, padding: "2px 4px 6px", borderBottom: "1px solid var(--border)", marginBottom: 4 }}>
            <button
              onClick={onSelectAll}
              style={{ background: "none", border: "none", padding: 0, fontSize: 11, color: "var(--accent)", cursor: "pointer" }}
            >
              Select all
            </button>
            <button
              onClick={onSelectNone}
              style={{ background: "none", border: "none", padding: 0, fontSize: 11, color: "var(--accent)", cursor: "pointer" }}
            >
              Select none
            </button>
          </div>

          {items.map((item) => (
            <label
              key={item}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "5px 6px",
                borderRadius: 6,
                fontSize: 12.5,
                color: "var(--text)",
                cursor: "pointer",
              }}
            >
              <input
                type="checkbox"
                checked={!hiddenItems.has(item)}
                onChange={() => onToggle(item)}
                style={{ width: "auto", flexShrink: 0, padding: 0, border: "none", background: "none" }}
              />
              <span
                style={{
                  fontFamily: "Manrope, sans-serif",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  minWidth: 0,
                  flex: 1,
                }}
              >
                {item}
              </span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
