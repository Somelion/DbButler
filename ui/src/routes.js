import { useMatch } from "react-router";
import {
  Activity,
  Archive,
  Calculator,
  ChartLine,
  Database,
  FileCode,
  FileText,
  FlaskConical,
  GitCompare,
  HeartPulse,
  History,
  LayoutDashboard,
  Lightbulb,
  Network,
  Rewind,
  SearchCode,
  Settings,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react";

// Every screen, in nav order. `scope: "target"` screens live under
// /t/:targetId/<key> and need a connected database; "global" ones are
// /<key>. `showMode` shows the Simple/Advanced toggle in the header.
export const SCREENS = [
  { key: "dashboard", label: "Dashboard", group: "monitor", icon: LayoutDashboard, scope: "target", showMode: true },
  { key: "activity", label: "Activity", group: "monitor", icon: Activity, scope: "target", showMode: true },
  { key: "trends", label: "Trends", group: "monitor", icon: ChartLine, scope: "target", showMode: false },
  { key: "time-machine", label: "Time Machine", group: "monitor", icon: Rewind, scope: "target", showMode: false },
  { key: "reports", label: "Weekly Reports", group: "monitor", icon: FileText, scope: "target", showMode: false },
  { key: "workload-map", label: "Workload Map", group: "analyze", icon: Network, scope: "target", showMode: false },
  { key: "table-health", label: "Table Health", group: "analyze", icon: HeartPulse, scope: "target", showMode: true },
  {
    key: "query-intelligence",
    label: "Query Intelligence",
    group: "analyze",
    icon: SearchCode,
    scope: "target",
    showMode: true,
  },
  { key: "query-history", label: "Query History", group: "analyze", icon: History, scope: "target", showMode: false },
  {
    key: "plan-regressions",
    label: "Plan Regressions",
    group: "analyze",
    icon: GitCompare,
    scope: "target",
    showMode: true,
  },
  { key: "ai-analysis", label: "AI Analysis", group: "analyze", icon: Sparkles, scope: "target", showMode: false },
  { key: "advisor", label: "Advisor", group: "advise", icon: Lightbulb, scope: "target", showMode: true },
  { key: "what-if", label: "What-if Indexes", group: "advise", icon: Calculator, scope: "target", showMode: false },
  {
    key: "migration-lint",
    label: "Migration Linter",
    group: "advise",
    icon: FileCode,
    scope: "target",
    showMode: false,
  },
  {
    key: "config-tuning",
    label: "Config Tuning",
    group: "advise",
    icon: SlidersHorizontal,
    scope: "target",
    showMode: true,
  },
  { key: "index-testing", label: "Index Testing", group: "advise", icon: FlaskConical, scope: "target", showMode: false },
  { key: "archive", label: "Archive", group: "advise", icon: Archive, scope: "target", showMode: true },
  { key: "connections", label: "Connections", group: "configure", icon: Database, scope: "global", showMode: false },
  { key: "settings", label: "Settings", group: "configure", icon: Settings, scope: "global", showMode: false },
];

export const NAV_GROUPS = [
  { key: "monitor", label: "Monitor" },
  { key: "analyze", label: "Analyze" },
  { key: "advise", label: "Advise" },
  { key: "configure", label: "Configure" },
];

export const SCREEN_BY_KEY = Object.fromEntries(SCREENS.map((s) => [s.key, s]));

// URL of a screen. `sub` is an optional extra path segment (the Advisor
// tab); `search` an optional query string object.
//   screenPath("advisor", id, "replication") -> /t/<id>/advisor/replication
export function screenPath(key, targetId, sub, search) {
  const screen = SCREEN_BY_KEY[key];
  let path =
    screen?.scope === "global" || !targetId
      ? `/${key}`
      : `/t/${encodeURIComponent(targetId)}/${key}${sub ? `/${encodeURIComponent(sub)}` : ""}`;
  if (search) {
    const params = new URLSearchParams(Object.entries(search).filter(([, v]) => v != null && v !== ""));
    const query = params.toString();
    if (query) path += `?${query}`;
  }
  return path;
}

// Which screen the current URL shows, plus the target id when it is a
// target-scoped one.
export function useCurrentScreen() {
  const targetMatch = useMatch("/t/:targetId/:screen/*");
  const globalMatch = useMatch("/:screen/*");
  if (targetMatch) {
    return { screenKey: targetMatch.params.screen, targetId: targetMatch.params.targetId };
  }
  const key = globalMatch?.params.screen;
  return { screenKey: SCREEN_BY_KEY[key]?.scope === "global" ? key : null, targetId: null };
}
