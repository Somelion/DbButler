import { NavLink } from "react-router";
import { useTranslation } from "react-i18next";
import styles from "./NavRail.module.css";
import { StatusDot } from "./components";
import { NAV_GROUPS, SCREENS, screenPath } from "./routes";

// Per-database screens link to the selected database and are disabled
// until it is connected; Connections and Settings are always reachable.
// Sticky beside the scrolling page; the theme toggle and the database
// switcher live in the top bar (ScreenHeader).
export default function NavRail({ connected, activeTarget, badges = {} }) {
  const { t } = useTranslation();
  return (
    <nav className={styles.rail} aria-label={t("nav.main")}>
      <div className={styles.brand}>
        <span className={styles.logoTile} aria-hidden="true">
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <ellipse cx="12" cy="5" rx="8" ry="3" />
            <path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5" />
            <path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6" />
          </svg>
        </span>
        <span className={styles.brandText}>
          <span className={styles.brandName}>DbButler</span>
          <span className={styles.instance}>
            <StatusDot
              tone={connected ? "healthy" : "unknown"}
              label={connected ? t("nav.connected") : t("nav.notConnected")}
              hideLabel
            />
            <span className={styles.instanceName} title={activeTarget ? `${activeTarget.host}:${activeTarget.port}` : undefined}>
              {activeTarget ? activeTarget.name : t("nav.noDatabase")}
            </span>
          </span>
        </span>
      </div>

      {NAV_GROUPS.map((group) => (
        <div key={group.key} className={styles.group}>
          <h2 className={styles.groupLabel} id={`nav-group-${group.key}`}>
            {t(`navGroups.${group.key}`)}
          </h2>
          <ul className={styles.list} aria-labelledby={`nav-group-${group.key}`}>
            {SCREENS.filter((s) => s.group === group.key).map((screen) => {
              const disabled = screen.scope === "target" && !connected;
              const badge = badges[screen.key] > 0 ? badges[screen.key] : null;
              const content = (
                <>
                  <screen.icon size={16} className={styles.icon} aria-hidden="true" />
                  <span className={styles.label}>{t(`screens.${screen.key}`)}</span>
                  {badge && (
                    <span className={styles.badge}>
                      {badge}
                      <span className="sr-only"> {t("nav.new")}</span>
                    </span>
                  )}
                </>
              );
              return (
                <li key={screen.key}>
                  {disabled ? (
                    <span className={`${styles.item} ${styles.disabled}`} aria-disabled="true" title={t("nav.connectFirst")}>
                      {content}
                    </span>
                  ) : (
                    <NavLink
                      to={screenPath(screen.key, activeTarget?.id)}
                      className={({ isActive }) => `${styles.item} ${isActive ? styles.active : ""}`}
                      title={t(`screens.${screen.key}`)}
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
    </nav>
  );
}
