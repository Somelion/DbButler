import { Monitor, Moon, Sun } from "lucide-react";
import styles from "./ScreenHeader.module.css";
import { SegmentedControl } from "./components";
import { useTheme } from "./theme";

const MODE_OPTIONS = [
  { value: "simple", label: "Simple" },
  { value: "advanced", label: "Advanced" },
];

const THEME_OPTIONS = [
  { value: "system", label: <Monitor size={14} aria-hidden="true" />, ariaLabel: "Match system theme" },
  { value: "light", label: <Sun size={14} aria-hidden="true" />, ariaLabel: "Light theme" },
  { value: "dark", label: <Moon size={14} aria-hidden="true" />, ariaLabel: "Dark theme" },
];

export default function ScreenHeader({ title, subtitle, mode, onModeChange, targetSwitcher }) {
  const { theme, setTheme } = useTheme();
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
        <SegmentedControl label="Theme" size="sm" options={THEME_OPTIONS} value={theme} onChange={setTheme} />
      </div>
    </header>
  );
}
