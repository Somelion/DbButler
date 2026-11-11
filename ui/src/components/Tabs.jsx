import { useRef } from "react";
import styles from "./SegmentedControl.module.css";
import { nextRovingIndex } from "./rovingFocus";

// WAI-ARIA tablist. Looks like SegmentedControl, but announces as tabs and
// wires each tab to the panel it controls. Render the active panel with
// <TabPanel idPrefix={…} value={activeKey}>. Arrow keys move focus and
// activate (automatic activation — every panel here is cheap to show).
//
//   <Tabs label="Advisor sections" idPrefix="advisor" items={TABS}
//     value={tab} onChange={setTab} />
//   <TabPanel idPrefix="advisor" value={tab}>…</TabPanel>
export function Tabs({ label, idPrefix, items, value, onChange, className }) {
  const itemRefs = useRef([]);
  const selectedIndex = Math.max(
    0,
    items.findIndex((item) => item.key === value)
  );

  const handleKeyDown = (event, index) => {
    const next = nextRovingIndex(event.key, index, items.length);
    if (next === null) return;
    event.preventDefault();
    onChange(items[next].key);
    itemRefs.current[next]?.focus();
  };

  return (
    <div role="tablist" aria-label={label} className={[styles.group, className].filter(Boolean).join(" ")}>
      {items.map((item, index) => {
        const selected = item.key === value;
        return (
          <button
            key={item.key}
            ref={(el) => {
              itemRefs.current[index] = el;
            }}
            type="button"
            role="tab"
            id={tabId(idPrefix, item.key)}
            aria-selected={selected}
            aria-controls={panelId(idPrefix)}
            tabIndex={index === selectedIndex ? 0 : -1}
            className={styles.item}
            onClick={() => onChange(item.key)}
            onKeyDown={(event) => handleKeyDown(event, index)}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

export function TabPanel({ idPrefix, value, className, children }) {
  return (
    <div role="tabpanel" id={panelId(idPrefix)} aria-labelledby={tabId(idPrefix, value)} className={className}>
      {children}
    </div>
  );
}

const tabId = (idPrefix, key) => `${idPrefix}-tab-${key}`;
const panelId = (idPrefix) => `${idPrefix}-panel`;
