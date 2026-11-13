import { lazy, Suspense, useEffect, useState } from "react";
import { Navigate, Outlet, Route, Routes, useNavigate, useOutletContext, useParams } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import styles from "./App.module.css";
import NavRail from "./NavRail";
import ScreenHeader from "./ScreenHeader";
import TargetSwitcher from "./TargetSwitcher";
import { ModeContext, MODE_STORAGE_KEY } from "./ModeContext";
import { Alert, Skeleton, useToast } from "./components";
import { TARGETS_KEY, extensionStatusQuery, queryAnalysesQuery, useTargets } from "./queries";
import { SCREEN_BY_KEY, screenPath, useCurrentScreen } from "./routes";
import ConnectionsScreen from "./ConnectionsScreen";
import DashboardScreen from "./DashboardScreen";
import ActivityScreen from "./ActivityScreen";
import TableHealthScreen from "./TableHealthScreen";
import QueryIntelligenceScreen from "./QueryIntelligenceScreen";
import QueryHistoryScreen from "./QueryHistoryScreen";
import PlanRegressionsScreen from "./PlanRegressionsScreen";
import AiAnalysisScreen from "./AiAnalysisScreen";
import AdvisorScreen from "./AdvisorScreen";
import IndexTestingScreen from "./IndexTestingScreen";
import ArchiveScreen from "./ArchiveScreen";
import ConfigTuningScreen from "./ConfigTuningScreen";
import TrendsScreen from "./TrendsScreen";
import TimeMachineScreen from "./TimeMachineScreen";
import WhatIfScreen from "./WhatIfScreen";
import MigrationLintScreen from "./MigrationLintScreen";
import ReportsScreen from "./ReportsScreen";
import Settings from "./Settings";

// The graph library (cytoscape + fcose) is the biggest dependency in the
// app; it loads only when someone opens the Workload Map.
const WorkloadMapScreen = lazy(() => import("./WorkloadMapScreen"));

function LazyWorkloadMap(props) {
  return (
    <Suspense fallback={<Skeleton height={420} label="Loading the Workload Map…" />}>
      <WorkloadMapScreen {...props} />
    </Suspense>
  );
}

const AI_ANALYSIS_POLL_MS = 8000;

// The database last looked at. Survives a reload, so "/" lands back on it;
// falls back to the first target if it no longer exists.
const ACTIVE_TARGET_STORAGE_KEY = "pgdba.activeTargetId";

function readStored(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key, value) {
  try {
    if (value) localStorage.setItem(key, value);
  } catch {
    // Storage blocked — the preference just won't persist.
  }
}

// URL layout:
//   /                         -> last-used database's dashboard (or Connections)
//   /connections, /settings   -> global screens
//   /t/:targetId/<screen>     -> everything else, per database
//   /t/:targetId/advisor/:tab -> one Advisor tab
// Screen-internal view state worth sharing (time range, selected table or
// query) lives in the query string, owned by each screen.
export default function App() {
  const targetsQuery = useTargets();

  if (targetsQuery.isPending) {
    return (
      <div className={styles.app}>
        <NavRail connected={false} activeTarget={null} />
        <main className={styles.main}>
          <Skeleton lines={4} label="Loading connections…" />
        </main>
      </div>
    );
  }

  const targets = targetsQuery.data ?? [];

  return (
    <Routes>
      <Route element={<Shell targets={targets} loadError={targetsQuery.error?.message} />}>
        <Route index element={<HomeRedirect />} />
        <Route path="connections" element={<ConnectionsRoute targets={targets} />} />
        <Route path="settings" element={<Settings />} />
        <Route path="t/:targetId" element={<TargetRoute />}>
          <Route index element={<Navigate to="dashboard" replace />} />
          <Route path="dashboard" element={<TargetScreen component={DashboardScreen} />} />
          <Route path="activity" element={<TargetScreen component={ActivityScreen} />} />
          <Route path="trends" element={<TargetScreen component={TrendsScreen} />} />
          <Route path="time-machine" element={<TargetScreen component={TimeMachineScreen} />} />
          <Route path="reports" element={<TargetScreen component={ReportsScreen} />} />
          <Route path="workload-map" element={<TargetScreen component={LazyWorkloadMap} />} />
          <Route path="table-health" element={<TargetScreen component={TableHealthScreen} />} />
          <Route path="query-intelligence" element={<TargetScreen component={QueryIntelligenceScreen} />} />
          <Route path="query-history" element={<TargetScreen component={QueryHistoryScreen} />} />
          <Route path="plan-regressions" element={<TargetScreen component={PlanRegressionsScreen} />} />
          <Route path="ai-analysis" element={<TargetScreen component={AiAnalysisScreen} />} />
          <Route path="advisor/:tab?" element={<TargetScreen component={AdvisorScreen} />} />
          <Route path="config-tuning" element={<TargetScreen component={ConfigTuningScreen} />} />
          <Route path="index-testing" element={<TargetScreen component={IndexTestingScreen} />} />
          <Route path="what-if" element={<TargetScreen component={WhatIfScreen} />} />
          <Route path="migration-lint" element={<TargetScreen component={MigrationLintScreen} />} />
          <Route path="archive" element={<TargetScreen component={ArchiveScreen} />} />
          <Route path="*" element={<TargetFallback />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

// Layout route: rail, banners, header, then the matched screen.
function Shell({ targets, loadError }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();
  const { screenKey, targetId: urlTargetId } = useCurrentScreen();

  // The selected database: whatever the URL names, else the last one picked
  // (on /connections and /settings). On a per-database URL the URL always
  // wins — including after Back/Forward from a global screen where a
  // different database was picked — so the header can never disagree with
  // the screen. Synced during render (React's "adjust state on prop change"
  // pattern), not in an effect; every switch made on a per-database screen
  // navigates, so this never fights the user.
  const [selectedTargetId, setSelectedTargetId] = useState(
    () => urlTargetId ?? readStored(ACTIVE_TARGET_STORAGE_KEY)
  );
  if (urlTargetId && urlTargetId !== selectedTargetId) setSelectedTargetId(urlTargetId);

  useEffect(() => {
    writeStored(ACTIVE_TARGET_STORAGE_KEY, selectedTargetId);
  }, [selectedTargetId]);

  const target = targets.find((t) => t.id === selectedTargetId) ?? targets[0] ?? null;
  const connected = Boolean(target?.last_test_ok);
  const screen = SCREEN_BY_KEY[screenKey] ?? SCREEN_BY_KEY.connections;

  const [mode, setModeState] = useState(() => (readStored(MODE_STORAGE_KEY) === "advanced" ? "advanced" : "simple"));
  const setMode = (next) => {
    setModeState(next);
    writeStored(MODE_STORAGE_KEY, next);
  };

  // "Your AI analysis is ready", surfaced on whatever screen the user is on.
  // Polls the same cached query the AI Analysis screen reads, and watches
  // the cache for pending -> done transitions (a cache subscription, so the
  // state update happens in a callback rather than synchronously in an
  // effect).
  const [aiAnalysisUnseen, setAiAnalysisUnseen] = useState(0);
  if (screenKey === "ai-analysis" && aiAnalysisUnseen !== 0) setAiAnalysisUnseen(0);

  useQuery({
    ...queryAnalysesQuery(target?.id),
    enabled: connected,
    refetchInterval: AI_ANALYSIS_POLL_MS,
  });

  useEffect(() => {
    if (!connected) return undefined;
    const watchedId = target.id;
    const seenStatuses = new Map();
    return queryClient.getQueryCache().subscribe((event) => {
      if (event.type !== "updated" || event.action?.type !== "success") return;
      const [scope, id, resource] = event.query.queryKey;
      if (scope !== "target" || id !== watchedId || resource !== "query-analyses") return;
      let newlyDone = 0;
      for (const analysis of event.query.state.data?.analyses ?? []) {
        const previous = seenStatuses.get(analysis.id);
        if (previous === "pending" && analysis.status !== "pending") newlyDone += 1;
        seenStatuses.set(analysis.id, analysis.status);
      }
      if (newlyDone > 0) {
        setAiAnalysisUnseen((n) => n + newlyDone);
        toast(
          newlyDone === 1
            ? "An AI query analysis is ready — check the AI Analysis screen."
            : `${newlyDone} AI query analyses are ready — check the AI Analysis screen.`
        );
      }
    });
  }, [connected, target?.id, queryClient, toast]);

  // Every-screen banner for "this target has no pg_stat_statements" — the
  // Dashboard lists it as a finding, but that's invisible elsewhere.
  const extensionStatus = useQuery({ ...extensionStatusQuery(target?.id), enabled: connected });
  const extensionWarning =
    connected && extensionStatus.data && !extensionStatus.data.pg_stat_statements_enabled
      ? extensionStatus.data.message
      : null;

  // Switching database keeps you on the same screen when you're on a
  // per-database one; a database that isn't connected goes to Connections,
  // where its card shows the failure and a Retest button.
  const handleSelectTarget = (id) => {
    setSelectedTargetId(id);
    const next = targets.find((t) => t.id === id);
    if (!next?.last_test_ok) navigate("/connections");
    else if (screen.scope === "target") navigate(screenPath(screen.key, id));
  };

  const subtitle =
    screen.scope === "target" && target
      ? `${target.name} · ${target.host}:${target.port}/${target.dbname} · PostgreSQL ${target.detected_pg_version}`
      : undefined;

  const outletContext = { targets, selectedTargetId: target?.id ?? null, setSelectedTargetId };

  return (
    <div className={styles.app}>
      <a href="#main-content" className={styles.skipLink}>
        Skip to content
      </a>
      <NavRail connected={connected} activeTarget={target} badges={{ "ai-analysis": aiAnalysisUnseen }} />
      <main id="main-content" className={styles.main} tabIndex={-1}>
        <div className={styles.topbar}>
          <div className={styles.topbarInner}>
            <ScreenHeader
              title={screen.label}
              subtitle={subtitle}
              mode={mode}
              onModeChange={screen.showMode ? setMode : null}
              targetSwitcher={
                <TargetSwitcher
                  targets={targets}
                  activeTargetId={target?.id ?? null}
                  onSelect={handleSelectTarget}
                  onManage={() => navigate("/connections")}
                />
              }
            />
          </div>
        </div>
        {/* Keyed by screen, so each screen fades in on navigation. */}
        <div className={styles.content} key={screenKey ?? "none"}>
          {loadError && <Alert tone="critical">{loadError}</Alert>}
          {extensionWarning && screen.scope === "target" && <Alert tone="attention">{extensionWarning}</Alert>}
          <ModeContext.Provider value={mode}>
            <Outlet context={outletContext} />
          </ModeContext.Provider>
        </div>
      </main>
    </div>
  );
}

// Guards read the targets straight from the query cache rather than a prop
// from App's last render: right after a connection is created, the
// navigation can render before App does, and a stale list would bounce the
// brand-new database back to Connections.
function HomeRedirect() {
  const targets = useTargets().data ?? [];
  const stored = readStored(ACTIVE_TARGET_STORAGE_KEY);
  const landing = targets.find((t) => t.id === stored) ?? targets[0];
  if (landing?.last_test_ok) return <Navigate to={screenPath("dashboard", landing.id)} replace />;
  return <Navigate to="/connections" replace />;
}

// /t/:targetId — guards the per-database screens: an unknown id or a
// database that isn't connected goes to Connections instead.
function TargetRoute() {
  const { targetId } = useParams();
  const parent = useOutletContext();
  const targets = useTargets().data ?? [];
  const target = targets.find((t) => t.id === targetId);
  if (!target || !target.last_test_ok) return <Navigate to="/connections" replace />;
  return <Outlet context={{ ...parent, target }} />;
}

// Unknown screen under /t/:targetId (typo, old bookmark). Absolute target:
// a relative "dashboard" inside a splat route resolves against the whole
// unmatched URL and would redirect forever (/t/x/typo/dashboard/...).
function TargetFallback() {
  const { targetId } = useParams();
  return <Navigate to={screenPath("dashboard", targetId)} replace />;
}

// Renders a per-database screen keyed by the target id, so switching
// database remounts it with fresh local state.
function TargetScreen({ component: Screen }) {
  const { target } = useOutletContext();
  return <Screen key={target.id} targetId={target.id} target={target} />;
}

// Connections keeps its callback props; they now write straight into the
// targets query cache.
function ConnectionsRoute({ targets }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { selectedTargetId, setSelectedTargetId } = useOutletContext();

  const updateTargets = (updater) => queryClient.setQueryData(TARGETS_KEY, (prev = []) => updater(prev));

  const handleCreated = (created) => {
    updateTargets((prev) => [...prev, created]);
    setSelectedTargetId(created.id);
    navigate(screenPath("dashboard", created.id));
  };

  const handleRemoved = (id) => {
    const remaining = targets.filter((t) => t.id !== id);
    updateTargets((prev) => prev.filter((t) => t.id !== id));
    queryClient.removeQueries({ queryKey: ["target", id] });
    if (selectedTargetId === id) setSelectedTargetId(remaining[0]?.id ?? null);
  };

  // Shared by retest, rename, pause/resume and the schema allowlist — all
  // "merge this partial update into the matching target by id," never a
  // full replace. A changed allowlist changes what every screen computes, so
  // that database's cached data is dropped to stale.
  const handleUpdated = (id, updated) => {
    const before = targets.find((t) => t.id === id);
    updateTargets((prev) => prev.map((t) => (t.id === id ? { ...t, ...updated } : t)));
    const schemasChanged =
      updated && "allowed_schemas" in updated &&
      JSON.stringify(updated.allowed_schemas ?? null) !== JSON.stringify(before?.allowed_schemas ?? null);
    if (schemasChanged) queryClient.invalidateQueries({ queryKey: ["target", id] });
  };

  return (
    <ConnectionsScreen
      targets={targets}
      activeTargetId={selectedTargetId}
      onCreated={handleCreated}
      onRemoved={handleRemoved}
      onUpdated={handleUpdated}
      onSelect={setSelectedTargetId}
    />
  );
}
