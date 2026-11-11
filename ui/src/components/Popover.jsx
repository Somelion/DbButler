import { useEffect, useId, useRef, useState } from "react";
import styles from "./Popover.module.css";

// A button that toggles a floating, non-modal panel (filter dropdowns,
// the database switcher). The trigger carries aria-expanded/aria-controls;
// Esc, a click outside or tabbing away closes the panel. Closing from
// inside the panel (Esc, or calling `close`) returns focus to the trigger.
// `children` is a render prop that receives `close`.
//
//   <Popover label={<><Settings2 /> Customize</>} buttonClassName="button-secondary button-sm">
//     {({ close }) => <label className={popoverStyles.option}>…</label>}
//   </Popover>
//
// Use the exported `popoverStyles.option` / `.divider` / `.sectionLabel`
// classes for the rows inside the panel.
export default function Popover({
  label,
  ariaLabel,
  buttonClassName,
  panelClassName,
  panelLabel,
  align = "end",
  children,
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const buttonRef = useRef(null);
  const wasOpenRef = useRef(false);
  const panelId = useId();

  useEffect(() => {
    if (!open) return undefined;
    const root = rootRef.current;
    const handlePointerDown = (event) => {
      if (root && !root.contains(event.target)) setOpen(false);
    };
    const handleKeyDown = (event) => {
      if (event.key === "Escape") setOpen(false);
    };
    const handleFocusOut = (event) => {
      if (event.relatedTarget && root && !root.contains(event.relatedTarget)) setOpen(false);
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    root?.addEventListener("focusout", handleFocusOut);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
      root?.removeEventListener("focusout", handleFocusOut);
    };
  }, [open]);

  // On close, if focus was inside the (now unmounted) panel it has fallen
  // back to <body> — put it on the trigger instead.
  useEffect(() => {
    if (open) {
      wasOpenRef.current = true;
      return;
    }
    if (!wasOpenRef.current) return;
    wasOpenRef.current = false;
    const active = document.activeElement;
    if (!active || active === document.body || rootRef.current?.contains(active)) buttonRef.current?.focus();
  }, [open]);

  const close = () => setOpen(false);

  return (
    <div ref={rootRef} className={styles.root}>
      <button
        ref={buttonRef}
        type="button"
        className={buttonClassName}
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((o) => !o)}
      >
        {label}
      </button>
      {open && (
        <div
          id={panelId}
          role="group"
          aria-label={panelLabel ?? (typeof ariaLabel === "string" ? ariaLabel : undefined)}
          className={[styles.panel, styles[align], panelClassName].filter(Boolean).join(" ")}
        >
          {children({ close })}
        </div>
      )}
    </div>
  );
}

export { styles as popoverStyles };
