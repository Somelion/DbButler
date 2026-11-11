import styles from "./ScreenHeader.module.css";
import { SegmentedControl } from "./components";

const MODE_OPTIONS = [
  { value: "simple", label: "Simple" },
  { value: "advanced", label: "Advanced" },
];

export default function ScreenHeader({ title, subtitle, mode, onModeChange, targetSwitcher }) {
  return (
    <header className={styles.header}>
      <div className={styles.titles}>
        <h1 className={styles.title}>{title}</h1>
        {subtitle && <p className={styles.subtitle}>{subtitle}</p>}
      </div>

      <div className={styles.controls}>
        {onModeChange && (
          <SegmentedControl label="Detail level" options={MODE_OPTIONS} value={mode} onChange={onModeChange} />
        )}
        {targetSwitcher}
      </div>
    </header>
  );
}
