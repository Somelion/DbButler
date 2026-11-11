import { useEffect, useId, useRef } from "react";
import { X } from "lucide-react";
import styles from "./Dialog.module.css";

// Modal dialog on the native <dialog> element: showModal() gives the focus
// trap, Esc handling and inert background for free. Focus goes to
// initialFocusRef (or the first focusable control) on open and back to
// whatever had it before on close.
//
//   <Dialog open={open} onClose={close} title="Analyze with AI"
//     footer={<button …>Run</button>}>…</Dialog>
export default function Dialog({
  open,
  onClose,
  title,
  description,
  footer,
  size = "md",
  initialFocusRef,
  className,
  children,
}) {
  const dialogRef = useRef(null);
  const returnFocusRef = useRef(null);
  const titleId = useId();
  const descriptionId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return undefined;
    if (open && !dialog.open) {
      returnFocusRef.current = document.activeElement;
      dialog.showModal();
      initialFocusRef?.current?.focus();
    } else if (!open && dialog.open) {
      dialog.close();
    }
    return undefined;
  }, [open, initialFocusRef]);

  // Return focus when the dialog closes or unmounts while open.
  useEffect(() => {
    if (open) return undefined;
    const previous = returnFocusRef.current;
    returnFocusRef.current = null;
    if (previous && typeof previous.focus === "function" && document.contains(previous)) previous.focus();
    return undefined;
  }, [open]);

  useEffect(
    () => () => {
      const previous = returnFocusRef.current;
      if (previous && typeof previous.focus === "function" && document.contains(previous)) previous.focus();
    },
    []
  );

  // Esc fires "cancel"; let the parent decide (it owns `open`).
  const handleCancel = (event) => {
    event.preventDefault();
    onClose?.();
  };

  // A click whose target is the <dialog> itself landed on the backdrop
  // (the content fills the box via .inner). Mouse-only convenience — Esc
  // and the close button are the keyboard equivalents.
  const handleBackdropClick = (event) => {
    if (event.target === dialogRef.current) onClose?.();
  };

  const classes = [styles.dialog, styles[size], className].filter(Boolean).join(" ");

  return (
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-noninteractive-element-interactions
    <dialog
      ref={dialogRef}
      className={classes}
      aria-labelledby={title ? titleId : undefined}
      aria-describedby={description ? descriptionId : undefined}
      onCancel={handleCancel}
      onClick={handleBackdropClick}
    >
      {open && (
        <div className={styles.inner}>
          {(title || onClose) && (
            <div className={styles.header}>
              <div>
                {title && (
                  <h2 id={titleId} className={styles.title}>
                    {title}
                  </h2>
                )}
                {description && (
                  <p id={descriptionId} className={styles.description}>
                    {description}
                  </p>
                )}
              </div>
              {onClose && (
                <button type="button" className="button-icon" onClick={onClose} aria-label="Close dialog">
                  <X size={16} aria-hidden="true" />
                </button>
              )}
            </div>
          )}
          <div className={styles.body}>{children}</div>
          {footer && <div className={styles.footer}>{footer}</div>}
        </div>
      )}
    </dialog>
  );
}
