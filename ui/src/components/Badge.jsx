import { CircleCheck, CircleHelp, CircleX, Info, TriangleAlert } from "lucide-react";
import i18n from "../i18n";
import styles from "./Badge.module.css";
import tone from "./tone.module.css";

// Severity vocabulary shared by every finding, tile and diagnose step.
// Each level carries a text label and an icon as well as a colour, so status
// is never conveyed by colour alone (WCAG 1.4.1). `label` is a getter, so it
// reads in the current UI language (common:severity.*).
const severityEntry = (key, Icon) => ({
  Icon,
  get label() {
    return i18n.t(`common:severity.${key}`);
  },
});

export const SEVERITY = {
  critical: severityEntry("critical", CircleX),
  attention: severityEntry("attention", TriangleAlert),
  healthy: severityEntry("healthy", CircleCheck),
  info: severityEntry("info", Info),
  unknown: severityEntry("unknown", CircleHelp),
};

export const severityMeta = (severity) => SEVERITY[severity] ?? SEVERITY.unknown;

// Pill with tinted background.
//   tone: critical | attention | healthy | info | unknown | neutral | accent
export function Badge({ tone: toneName = "neutral", icon: Icon, outlined = false, className, children, ...rest }) {
  const classes = [styles.badge, tone[toneName] ?? tone.neutral, outlined && styles.outlined, className]
    .filter(Boolean)
    .join(" ");
  return (
    <span className={classes} {...rest}>
      {Icon && <Icon size={11} strokeWidth={2.5} className={styles.icon} aria-hidden="true" />}
      {children}
    </span>
  );
}

// "Critical" / "Attention" / … pill with its icon.
export function SeverityBadge({ severity, label, className }) {
  const meta = severityMeta(severity);
  return (
    <Badge tone={SEVERITY[severity] ? severity : "unknown"} icon={meta.Icon} className={className}>
      {label ?? meta.label}
    </Badge>
  );
}

// Icon-only severity marker, announced by its label.
export function SeverityIcon({ severity, size = 14, className }) {
  const meta = severityMeta(severity);
  const toneClass = tone[SEVERITY[severity] ? severity : "unknown"];
  return (
    <meta.Icon
      size={size}
      strokeWidth={2.25}
      role="img"
      aria-label={meta.label}
      className={[styles.severityIcon, toneClass, className].filter(Boolean).join(" ")}
    />
  );
}

// Small coloured dot. Always pass a label: it is shown next to the dot, or
// (with hideLabel) kept for screen readers only.
export function StatusDot({ tone: toneName = "neutral", label, hideLabel = false, className }) {
  return (
    <span className={[styles.dotWrap, tone[toneName] ?? tone.neutral, className].filter(Boolean).join(" ")}>
      <span className={styles.dot} aria-hidden="true" />
      <span className={hideLabel ? "sr-only" : styles.dotLabel}>{label}</span>
    </span>
  );
}
