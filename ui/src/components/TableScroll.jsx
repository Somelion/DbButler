import { useEffect, useRef, useState } from "react";
import styles from "./TableScroll.module.css";

// A wide table that scrolls sideways. The page itself scrolls (one
// scrollbar at the window edge), so a long table's own horizontal scrollbar
// would sit at its very bottom, out of sight. This one stays pinned to the
// bottom of the window while the table is on screen, and drops back under
// the table when its end is reached. Hidden while the table fits.
// `label` names the scroll region, which takes keyboard focus (arrow keys
// scroll it) while the table is wider than the screen.
export default function TableScroll({ label, children }) {
  const contentRef = useRef(null);
  const barRef = useRef(null);
  const [size, setSize] = useState({ client: 0, scroll: 0 });

  useEffect(() => {
    const content = contentRef.current;
    const measure = () => setSize({ client: content.clientWidth, scroll: content.scrollWidth });
    // The wrapper changes with the window, the table inside it with its
    // rows (an expanded detail, a collapsed schema).
    const observer = new ResizeObserver(measure);
    observer.observe(content);
    for (const child of content.children) observer.observe(child);
    measure();
    return () => observer.disconnect();
  }, []);

  const overflows = size.scroll > size.client + 1;

  // Each side follows the other. Setting the same scrollLeft again fires no
  // scroll event, so this can't loop.
  const follow = (from, to) => {
    if (from.current && to.current && to.current.scrollLeft !== from.current.scrollLeft) {
      to.current.scrollLeft = from.current.scrollLeft;
    }
  };

  return (
    <div className={styles.root}>
      <div
        ref={contentRef}
        className={styles.content}
        onScroll={() => follow(contentRef, barRef)}
        role={overflows ? "region" : undefined}
        aria-label={overflows ? label : undefined}
        tabIndex={overflows ? 0 : undefined}
      >
        {children}
      </div>
      {overflows && (
        // A second handle on the same scroll: mouse only. Keyboard and
        // screen-reader users scroll the table itself.
        <div ref={barRef} className={styles.bar} onScroll={() => follow(barRef, contentRef)} aria-hidden="true">
          <div style={{ width: size.scroll }} className={styles.spacer} />
        </div>
      )}
    </div>
  );
}
