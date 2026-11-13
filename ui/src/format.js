import i18n from "./i18n";

export function formatBytes(bytes) {
  if (bytes == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

// In the current UI language (common:time.*). Called during render; the
// app remounts on a language switch (main.jsx), so these re-run then.
export function formatRelativeTime(isoString) {
  const t = i18n.t.bind(i18n);
  if (!isoString) return t("common:time.never");
  const diffSeconds = Math.max(0, Math.floor((Date.now() - new Date(isoString).getTime()) / 1000));
  if (diffSeconds < 60) return t("common:time.secondsAgo", { count: diffSeconds });
  const diffMinutes = Math.floor(diffSeconds / 60);
  if (diffMinutes < 60) return t("common:time.minutesAgo", { count: diffMinutes });
  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return t("common:time.hoursAgo", { count: diffHours });
  return t("common:time.daysAgo", { count: Math.floor(diffHours / 24) });
}

export function formatMs(ms) {
  if (ms == null) return "—";
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)}s`;
  return `${ms.toFixed(1)}ms`;
}

export function formatMetricValue(value, unit) {
  if (value == null) return "—";
  if (unit === "%") return `${value.toFixed(1)}%`;
  if (unit === "bytes") return formatBytes(value);
  if (unit === "ms") return formatMs(value);
  return Math.round(value).toLocaleString();
}
