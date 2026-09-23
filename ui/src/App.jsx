import { useEffect, useRef, useState } from "react";
import "./theme.css";
import { api } from "./api";
import NavRail from "./NavRail";
import ScreenHeader from "./ScreenHeader";
import TargetSwitcher from "./TargetSwitcher";
import { ModeContext } from "./ModeContext";
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
import Settings from "./Settings";

const AI_ANALYSIS_POLL_MS = 8000;

// Survives a page refresh so switching databases doesn't get undone by
// reloading — falls back to the first target if the stored id no longer
// exists (deleted since, or this is the very first load).
const ACTIVE_TARGET_STORAGE_KEY = "pgdba.activeTargetId";

const SCREEN_META = {
  connections: { title: "Connections", showMode: false },
  dashboard: { title: "Dashboard", showMode: true },
  activity: { title: "Activity", showMode: true },
  "table-health": { title: "Table Health", showMode: true },
  "query-intelligence": { title: "Query Intelligence", showMode: true },
  "query-history": { title: "Query History", showMode: false },
  "plan-regressions": { title: "Plan Regressions", showMode: true },
  "ai-analysis": { title: "AI Analysis", showMode: false },
  advisor: { title: "Advisor", showMode: true },
  "index-testing": { title: "Index Testing", showMode: false },
  archive: { title: "Archive", showMode: true },
  "config-tuning": { title: "Config Tuning", showMode: true },
  trends: { title: "Trends", showMode: false },
  settings: { title: "Settings", showMode: false },
};

export default function App() {
  const [loading, setLoading] = useState(true);
  const [targets, setTargets] = useState([]);
  const [activeTargetId, setActiveTargetId] = useState(() => localStorage.getItem(ACTIVE_TARGET_STORAGE_KEY));
  const [loadError, setLoadError] = useState(null);
  const [activeView, setActiveView] = useState("connections");
  // Set only by handleNavigate below (a Dashboard tile/finding's "Open X"
  // button) and consumed once by AdvisorScreen's lazy tab-init — a plain
  // NavRail click goes through handleSelectView instead, which clears this
  // so a stale hint from an earlier contextual jump can't silently reapply
  // itself the next time the user opens Advisor on their own.
  const [advisorInitialTab, setAdvisorInitialTab] = useState(null);
  const [mode, setMode] = useState("simple");
  const [aiAnalysisUnseen, setAiAnalysisUnseen] = useState(0);
  const [toast, setToast] = useState(null);
  const [extensionWarning, setExtensionWarning] = useState(null);
  const seenAnalysisStatuses = useRef(new Map());

  useEffect(() => {
    api
      .listTargets()
      .then((fetched) => {
        setTargets(fetched);
        // Keep the previously-active target if it still exists (e.g. after
        // a refresh); otherwise land on whichever sorts first.
        const stillExists = activeTargetId && fetched.some((t) => t.id === activeTargetId);
        const landingId = stillExists ? activeTargetId : fetched[0]?.id ?? null;
        setActiveTargetId(landingId);
        // Returning to an already-connected target should land on the
        // dashboard, not send them back through the connect form.
        if (fetched.find((t) => t.id === landingId)?.last_test_ok) setActiveView("dashboard");
      })
      .catch((err) => setLoadError(err.message))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (activeTargetId) localStorage.setItem(ACTIVE_TARGET_STORAGE_KEY, activeTargetId);
  }, [activeTargetId]);

  const target = targets.find((t) => t.id === activeTargetId) ?? null;
  const connected = Boolean(target?.last_test_ok);
  const view = connected ? activeView : "connections";
  const meta = SCREEN_META[view];

  // Polls independently of the AI Analysis screen itself, so a "your
  // analysis is ready" notification still surfaces while the user is on a
  // different screen — matches this app's existing polling architecture
  // (no websockets/SSE anywhere else) rather than adding a new mechanism.
  useEffect(() => {
    if (!connected) return undefined;

    const poll = () => {
      api
        .getQueryAnalyses(target.id)
        .then((data) => {
          let newlyDone = 0;
          for (const analysis of data.analyses) {
            const prevStatus = seenAnalysisStatuses.current.get(analysis.id);
            if (prevStatus === "pending" && analysis.status !== "pending") {
              newlyDone += 1;
            }
            seenAnalysisStatuses.current.set(analysis.id, analysis.status);
          }
          if (newlyDone > 0) {
            setAiAnalysisUnseen((n) => n + newlyDone);
            setToast(
              newlyDone === 1
                ? "An AI query analysis is ready — check the AI Analysis tab."
                : `${newlyDone} AI query analyses are ready — check the AI Analysis tab.`
            );
          }
        })
        .catch(() => {});
    };

    poll();
    const interval = setInterval(poll, AI_ANALYSIS_POLL_MS);
    return () => clearInterval(interval);
  }, [connected, target?.id]);

  // A global, every-screen banner for "this target has no pg_stat_statements"
  // — the Dashboard's own compute_health() already surfaces this as one
  // Finding in its own findings feed, but that's invisible on every other
  // screen. Re-checked on every target switch; not polled continuously since
  // whether the extension is installed rarely changes mid-session.
  useEffect(() => {
    if (!connected) {
      setExtensionWarning(null);
      return undefined;
    }
    let cancelled = false;
    api
      .getExtensionStatus(target.id)
      .then((status) => {
        if (!cancelled) setExtensionWarning(status.pg_stat_statements_enabled ? null : status.message);
      })
      .catch(() => {
        if (!cancelled) setExtensionWarning(null);
      });
    return () => {
      cancelled = true;
    };
  }, [connected, target?.id]);

  useEffect(() => {
    if (!toast) return undefined;
    const timeout = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(timeout);
  }, [toast]);

  const handleSelectView = (key) => {
    if (key === "ai-analysis") setAiAnalysisUnseen(0);
    setAdvisorInitialTab(null);
    setActiveView(key);
  };

  // A Dashboard tile/finding's "Open X" button — unlike handleSelectView
  // (plain NavRail navigation), this can also carry a hint for the
  // destination screen (currently just Advisor's tab) about where within
  // that screen to land.
  const handleNavigate = (key, { advisorTab } = {}) => {
    setAdvisorInitialTab(advisorTab ?? null);
    setActiveView(key);
  };

  // A viewport switch, not a monitoring on/off switch — every is_active
  // target keeps being collected/scanned/alerted-on in the background
  // regardless of which one is currently displayed (scheduler.py::
  // _active_targets loops all of them). Selecting a target that isn't
  // currently connected is deliberately allowed: `view` above already
  // falls back to the Connections screen for it, landing on that target's
  // own card so its failure is visible and retestable, same as today's
  // single-target flow just now reachable by selection instead of being
  // the only option.
  const handleSelectTarget = (id) => setActiveTargetId(id);

  const handleTargetCreated = (created) => {
    setTargets([...targets, created]);
    setActiveTargetId(created.id);
    setActiveView("dashboard");
  };

  const handleTargetRemoved = (id) => {
    const next = targets.filter((t) => t.id !== id);
    setTargets(next);
    if (activeTargetId === id) setActiveTargetId(next[0]?.id ?? null);
  };

  // Shared by retest, rename, and pause/resume — all three are "merge this
  // partial update into the matching target by id," never a full replace.
  const handleTargetUpdated = (id, updated) => {
    setTargets(targets.map((t) => (t.id === id ? { ...t, ...updated } : t)));
  };

  const subtitle =
    view !== "connections" && target
      ? `${target.name} · ${target.host}:${target.port}/${target.dbname} · PostgreSQL ${target.detected_pg_version}`
      : undefined;

  const renderScreen = () => {
    if (view === "connections") {
      return (
        <ConnectionsScreen
          targets={targets}
          activeTargetId={activeTargetId}
          onCreated={handleTargetCreated}
          onRemoved={handleTargetRemoved}
          onUpdated={handleTargetUpdated}
          onSelect={handleSelectTarget}
        />
      );
    }
    if (!connected) return null;
    switch (view) {
      case "dashboard":
        return <DashboardScreen targetId={target.id} onNavigate={handleNavigate} />;
      case "activity":
        return <ActivityScreen targetId={target.id} />;
      case "table-health":
        return <TableHealthScreen targetId={target.id} />;
      case "query-intelligence":
        return <QueryIntelligenceScreen targetId={target.id} />;
      case "query-history":
        return <QueryHistoryScreen targetId={target.id} />;
      case "plan-regressions":
        return <PlanRegressionsScreen targetId={target.id} />;
      case "ai-analysis":
        return <AiAnalysisScreen targetId={target.id} />;
      case "advisor":
        return <AdvisorScreen targetId={target.id} initialTab={advisorInitialTab} />;
      case "index-testing":
        return <IndexTestingScreen targetId={target.id} />;
      case "archive":
        return <ArchiveScreen targetId={target.id} />;
      case "config-tuning":
        return <ConfigTuningScreen targetId={target.id} />;
      case "trends":
        return <TrendsScreen targetId={target.id} />;
      case "settings":
        return <Settings />;
      default:
        return null;
    }
  };

  if (loading) {
    return (
      <div style={{ display: "flex", height: "100vh" }}>
        <NavRail active="connections" onSelect={() => {}} connected={false} />
        <main style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <span style={{ color: "var(--text-secondary)" }}>Loading&hellip;</span>
        </main>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", height: "100vh" }}>
      <NavRail
        active={view}
        onSelect={handleSelectView}
        connected={connected}
        badges={{ "ai-analysis": aiAnalysisUnseen }}
      />
      {toast && (
        <div
          style={{
            position: "fixed",
            bottom: 24,
            right: 24,
            zIndex: 1100,
            padding: "12px 16px",
            background: "var(--text)",
            color: "var(--card)",
            borderRadius: 10,
            fontSize: 12.5,
            maxWidth: 320,
            boxShadow: "0 4px 16px oklch(0% 0 0 / 20%)",
          }}
        >
          {toast}
        </div>
      )}
      <main
        style={{
          flex: 1,
          minWidth: 0,
          display: "flex",
          flexDirection: "column",
          gap: 26,
          padding: "28px 36px",
          overflow: "auto",
          boxSizing: "border-box",
        }}
      >
        {loadError && (
          <div
            style={{
              padding: "12px 16px",
              background: "var(--critical-bg)",
              border: "1px solid var(--critical-border)",
              borderRadius: 12,
              color: "var(--critical-text)",
              fontSize: 13,
            }}
          >
            {loadError}
          </div>
        )}
        {extensionWarning && (
          <div
            style={{
              padding: "12px 16px",
              background: "var(--attention-bg)",
              border: "1px solid var(--attention-border)",
              borderRadius: 12,
              color: "var(--attention-text)",
              fontSize: 13,
            }}
          >
            {extensionWarning}
          </div>
        )}
        <ScreenHeader
          title={meta.title}
          subtitle={subtitle}
          mode={mode}
          onModeChange={meta.showMode ? setMode : null}
          targetSwitcher={
            <TargetSwitcher
              targets={targets}
              activeTargetId={activeTargetId}
              onSelect={handleSelectTarget}
              onManage={() => setActiveView("connections")}
            />
          }
        />
        <ModeContext.Provider value={mode}>{renderScreen()}</ModeContext.Provider>
      </main>
    </div>
  );
}
