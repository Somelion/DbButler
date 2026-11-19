import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import styles from "./Popover.module.css";

const FLOATING_GAP = 6;

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
// classes for the rows inside the panel. `onOpen` runs each time the panel
// opens (e.g. to load its contents); `buttonProps` go onto the trigger (an
// id for a <label>, aria-describedby, disabled).
//
// `floating` places the panel against the window instead of the trigger's
// box, for a trigger inside a scrolling area (a table row) that would
// otherwise clip it: below the trigger, or above it when there's no room
// below. Scrolling anything outside the panel closes it, since it would
// no longer sit next to its trigger.
export default function Popover({
  label,
  ariaLabel,
  buttonClassName,
  buttonProps,
  className,
  panelClassName,
  panelLabel,
  align = "end",
  floating = false,
  onOpen,
  children,
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const buttonRef = useRef(null);
  const panelRef = useRef(null);
  const wasOpenRef = useRef(false);
  const panelId = useId();

  // Before paint, so the panel never shows in the wrong place first.
  useLayoutEffect(() => {
    if (!open || !floating) return undefined;
    const panel = panelRef.current;
    const button = buttonRef.current.getBoundingClientRect();
    const viewportWidth = document.documentElement.clientWidth;
    const viewportHeight = document.documentElement.clientHeight;
    const roomBelow = viewportHeight - button.bottom - FLOATING_GAP;
    const top =
      roomBelow >= panel.offsetHeight || roomBelow >= button.top
        ? button.bottom + FLOATING_GAP
        : button.top - FLOATING_GAP - panel.offsetHeight;
    const left = align === "end" ? button.right - panel.offsetWidth : button.left;
    panel.style.top = `${Math.max(FLOATING_GAP, top)}px`;
    panel.style.left = `${Math.min(Math.max(FLOATING_GAP, left), viewportWidth - panel.offsetWidth - FLOATING_GAP)}px`;

    const closeUnlessInside = (event) => {
      if (!panel.contains(event.target)) setOpen(false);
    };
    const closeOnResize = () => setOpen(false);
    window.addEventListener("scroll", closeUnlessInside, true);
    window.addEventListener("resize", closeOnResize);
    return () => {
      window.removeEventListener("scroll", closeUnlessInside, true);
      window.removeEventListener("resize", closeOnResize);
    };
  }, [open, floating, align]);

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
    <div ref={rootRef} className={[styles.root, className].filter(Boolean).join(" ")}>
      <button
        {...buttonProps}
        ref={buttonRef}
        type="button"
        className={buttonClassName}
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => {
          if (!open) onOpen?.();
          setOpen(!open);
        }}
      >
        {label}
      </button>
      {open && (
        <div
          ref={panelRef}
          id={panelId}
          role="group"
          aria-label={panelLabel ?? (typeof ariaLabel === "string" ? ariaLabel : undefined)}
          className={[styles.panel, floating ? styles.floating : styles[align], panelClassName]
            .filter(Boolean)
            .join(" ")}
        >
          {children({ close })}
        </div>
      )}
    </div>
  );
}

export { styles as popoverStyles };
