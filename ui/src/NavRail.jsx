import { NavLink } from "react-router";
import { Monitor, Moon, Sun } from "lucide-react";
import styles from "./NavRail.module.css";
import { SegmentedControl, StatusDot } from "./components";
import { NAV_GROUPS, SCREENS, screenPath } from "./routes";
import { useTheme } from "./theme";

const THEME_OPTIONS = [
  { value: "system", label: <Monitor size={14} aria-hidden="true" />, ariaLabel: "Match system theme" },
  { value: "light", label: <Sun size={14} aria-hidden="true" />, ariaLabel: "Light theme" },
  { value: "dark", label: <Moon size={14} aria-hidden="true" />, ariaLabel: "Dark theme" },
];

// Per-database screens link to the selected database and are disabled
// until it is connected; Connections and Settings are always reachable.
export default function NavRail({ connected, activeTarget, badges = {} }) {
  const { theme, setTheme } = useTheme();

  return (
    <nav className={styles.rail} aria-label="Main">
      <div className={styles.top}>
        <div className={styles.brand}>
          <svg
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className={styles.logo}
            aria-hidden="true"
          >
            <ellipse cx="12" cy="5" rx="8" ry="3" />
            <path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5" />
            <path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6" />
          </svg>
          <span className={styles.brandName}>DbButler</span>
        </div>

        {NAV_GROUPS.map((group) => (
          <div key={group.key} className={styles.group}>
            <h2 className={styles.groupLabel} id={`nav-group-${group.key}`}>
              {group.label}
            </h2>
            <ul className={styles.list} aria-labelledby={`nav-group-${group.key}`}>
              {SCREENS.filter((s) => s.group === group.key).map((screen) => {
                const disabled = screen.scope === "target" && !connected;
                const badge = badges[screen.key] > 0 ? badges[screen.key] : null;
                const content = (
                  <>
                    <screen.icon size={16} className={styles.icon} aria-hidden="true" />
                    <span className={styles.label}>{screen.label}</span>
                    {badge && (
                      <span className={styles.badge}>
                        {badge}
                        <span className="sr-only"> new</span>
                      </span>
                    )}
                  </>
                );
                return (
                  <li key={screen.key}>
                    {disabled ? (
                      <span
                        className={`${styles.item} ${styles.disabled}`}
                        aria-disabled="true"
                        title="Connect a database first"
                      >
                        {content}
                      </span>
                    ) : (
                      <NavLink
                        to={screenPath(screen.key, activeTarget?.id)}
                        className={({ isActive }) => `${styles.item} ${isActive ? styles.active : ""}`}
                        title={screen.label}
                      >
                        {content}
                      </NavLink>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>

      <div className={styles.footer}>
        <div className={styles.instance}>
          <StatusDot
            tone={connected ? "healthy" : "unknown"}
            label={connected ? "Connected" : "Not connected"}
            hideLabel
          />
          <span className={styles.instanceName} title={activeTarget ? `${activeTarget.host}:${activeTarget.port}` : undefined}>
            {activeTarget ? activeTarget.name : "No database"}
          </span>
        </div>
        <SegmentedControl label="Theme" size="sm" options={THEME_OPTIONS} value={theme} onChange={setTheme} />
      </div>
    </nav>
  );
}
