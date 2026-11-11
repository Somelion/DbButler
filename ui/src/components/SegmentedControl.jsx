import { useRef } from "react";
import styles from "./SegmentedControl.module.css";
import { nextRovingIndex } from "./rovingFocus";

// A single-choice pill strip (time range, metric, Simple/Advanced). Behaves
// as a radio group: one Tab stop, arrow keys move and select.
//
//   <SegmentedControl label="Time range" value={hours} onChange={setHours}
//     options={[{ value: 1, label: "1h" }, { value: 24, label: "24h" }]} />
//
// An option whose label is only an icon needs `ariaLabel`.
export default function SegmentedControl({ label, options, value, onChange, size = "md", className }) {
  const itemRefs = useRef([]);
  const selectedIndex = Math.max(
    0,
    options.findIndex((o) => o.value === value)
  );

  const handleKeyDown = (event, index) => {
    const next = nextRovingIndex(event.key, index, options.length);
    if (next === null) return;
    event.preventDefault();
    onChange(options[next].value);
    itemRefs.current[next]?.focus();
  };

  const classes = [styles.group, size === "sm" && styles.small, className].filter(Boolean).join(" ");

  return (
    <div role="radiogroup" aria-label={label} className={classes}>
      {options.map((option, index) => {
        const checked = option.value === value;
        return (
          <button
            key={String(option.value)}
            ref={(el) => {
              itemRefs.current[index] = el;
            }}
            type="button"
            role="radio"
            aria-checked={checked}
            aria-label={option.ariaLabel}
            title={option.ariaLabel}
            tabIndex={index === selectedIndex ? 0 : -1}
            className={styles.item}
            onClick={() => onChange(option.value)}
            onKeyDown={(event) => handleKeyDown(event, index)}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
