import { cloneElement, useId } from "react";
import styles from "./Field.module.css";

// Label + control + optional hint/error, wired together: the <label> points
// at the control, and the hint/error are its aria-describedby. Pass exactly
// one control element as the child.
//
//   <Field label="Host" hint="Hostname or IP">
//     <input value={host} onChange={…} />
//   </Field>
export function Field({ label, hint, error, className, children }) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint && hintId, error && errorId].filter(Boolean).join(" ") || undefined;

  return (
    <div className={[styles.field, className].filter(Boolean).join(" ")}>
      <label htmlFor={id} className={styles.label}>
        {label}
      </label>
      {cloneElement(children, {
        id,
        "aria-describedby": describedBy,
        "aria-invalid": error ? true : undefined,
      })}
      {hint && (
        <span id={hintId} className={styles.hint}>
          {hint}
        </span>
      )}
      {error && (
        <span id={errorId} className={styles.error}>
          {error}
        </span>
      )}
    </div>
  );
}

// Checkbox with its label wrapped around it (the whole line is clickable).
export function Checkbox({ checked, onChange, children, className, ...rest }) {
  return (
    <label className={[styles.checkboxRow, className].filter(Boolean).join(" ")}>
      <input type="checkbox" checked={checked} onChange={onChange} {...rest} />
      {children}
    </label>
  );
}

export { styles as fieldStyles };
