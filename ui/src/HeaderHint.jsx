import { CircleHelp } from "lucide-react";
import styles from "./HeaderHint.module.css";

// Small "?" next to a column header. It sits *inside* the header's sort
// button, so it stays non-interactive phrasing content (no click handler,
// no tab stop): the native tooltip is for mouse users, the sr-only copy
// makes the same text part of the button's accessible description.
export default function HeaderHint({ text }) {
  if (!text) return null;
  return (
    <span className={styles.hint} title={text}>
      <CircleHelp size={13} aria-hidden="true" />
      <span className="sr-only">{text}</span>
    </span>
  );
}
