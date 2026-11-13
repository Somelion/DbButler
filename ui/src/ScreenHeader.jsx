import { useTranslation } from "react-i18next";
import { Monitor, Moon, Sun } from "lucide-react";
import styles from "./ScreenHeader.module.css";
import { SegmentedControl } from "./components";
import { LANGUAGES, setLanguage } from "./i18n";
import { useTheme } from "./theme";

const LANGUAGE_OPTIONS = LANGUAGES.map((l) => ({ value: l.code, label: l.label, ariaLabel: l.name }));

export default function ScreenHeader({ title, subtitle, mode, onModeChange, targetSwitcher }) {
  const { t, i18n } = useTranslation();
  const { theme, setTheme } = useTheme();
  const modeOptions = [
    { value: "simple", label: t("header.simple") },
    { value: "advanced", label: t("header.advanced") },
  ];
  const themeOptions = [
    { value: "system", label: <Monitor size={14} aria-hidden="true" />, ariaLabel: t("header.themeSystem") },
    { value: "light", label: <Sun size={14} aria-hidden="true" />, ariaLabel: t("header.themeLight") },
    { value: "dark", label: <Moon size={14} aria-hidden="true" />, ariaLabel: t("header.themeDark") },
  ];
  return (
    <header className={styles.header}>
      <div className={styles.titles}>
        <h1 className={styles.title}>{title}</h1>
        {subtitle && <p className={styles.subtitle}>{subtitle}</p>}
      </div>

      <div className={styles.controls}>
        {onModeChange && (
          <SegmentedControl label={t("header.detailLevel")} options={modeOptions} value={mode} onChange={onModeChange} />
        )}
        {targetSwitcher}
        <SegmentedControl
          label={t("header.language")}
          size="sm"
          options={LANGUAGE_OPTIONS}
          value={i18n.resolvedLanguage ?? i18n.language}
          onChange={setLanguage}
        />
        <SegmentedControl label={t("header.theme")} size="sm" options={themeOptions} value={theme} onChange={setTheme} />
      </div>
    </header>
  );
}
