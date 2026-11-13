import i18n from "./i18n";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { Maximize2, Minimize2, Network, Search } from "lucide-react";
import styles from "./WorkloadMapScreen.module.css";
import { api } from "./api";
import { formatRelativeTime } from "./format";
import { targetKey } from "./queryClient";
import { Alert, Card, Checkbox, EmptyState, SegmentedControl, Skeleton, Tabs, TabPanel } from "./components";
import WorkloadGraph from "./WorkloadGraph";

// three.js is big; it loads only when someone switches to 3D.
const WorkloadGraph3D = lazy(() => import("./WorkloadGraph3D"));
import WorkloadRelationsTable from "./WorkloadRelationsTable";
import WorkloadSidePanel from "./WorkloadSidePanel";
import { COLOR_MODES, COLOR_MODE_DESCRIPTIONS, EDGE_KINDS, EDGE_KIND_KEYS } from "./workloadKinds";

// Labels are translation keys (workloadMap:ranges.* / views.*), read at render.
const RANGE_OPTIONS = [{ value: 24 }, { value: 168 }, { value: 720 }];
const DEFAULT_HOURS = 168;
const VIEW_ITEMS = [{ key: "map" }, { key: "3d" }, { key: "list" }];
const MAX_HISTORY_HOURS = 720; // the store keeps 30 days

// View state lives in the URL so a map can be shared or reloaded as-is:
//   ?hours=24|168|720    window the weights come from (7 days by default)
//   ?until=<ISO>         window end, from the time slider (absent = now)
//   ?view=list|3d        the Relationships table, or the 3D view
//   ?color=seq|writes    node colouring (health by default)
//   ?hide=fk_unused,…    edge kinds hidden
//   ?table=schema.table  or ?edge=<id> — what the side panel shows
// visibleKinds and selection are memoized on their URL values: the graph
// re-runs its selection effect (which recentres the view) whenever the
// selection object changes, and a fresh object every render would snap the
// view back on each hover.
function useMapParams(searchParams) {
  const hours = Number(searchParams.get("hours"));
  const color = searchParams.get("color");
  const hideParam = searchParams.get("hide") ?? "";
  const table = searchParams.get("table");
  const edge = searchParams.get("edge");
  const visibleKinds = useMemo(() => {
    const hidden = new Set(hideParam.split(","));
    return new Set(EDGE_KIND_KEYS.filter((k) => !hidden.has(k)));
  }, [hideParam]);
  const selection = useMemo(
    () => (table ? { type: "table", id: table } : edge ? { type: "edge", id: edge } : null),
    [table, edge]
  );
  const viewParam = searchParams.get("view");
  const untilParam = searchParams.get("until");
  const untilDate = untilParam ? new Date(untilParam) : null;
  return {
    hours: RANGE_OPTIONS.some((o) => o.value === hours) ? hours : DEFAULT_HOURS,
    until: untilDate && !Number.isNaN(untilDate.getTime()) ? untilDate : null,
    view: viewParam === "list" || viewParam === "3d" ? viewParam : "map",
    colorBy: COLOR_MODES.some((m) => m.value === color) ? color : "health",
    visibleKinds,
    selection,
  };
}

function CoverageMeter({ meta }) {
  const { t } = useTranslation("workloadMap");
  const counts = meta.query_status_counts;
  const coverage = meta.coverage_pct;
  const notes = [];
  if (counts.needs_generic_plan > 0) {
    notes.push(t("coverage.needsGenericPlan", { count: counts.needs_generic_plan }));
  }
  if (counts.explain_failed > 0) {
    notes.push(t("coverage.explainFailed", { count: counts.explain_failed }));
  }
  if (counts.not_planned_yet > 0) notes.push(t("coverage.notPlannedYet", { count: counts.not_planned_yet }));

  return (
    <div className={styles.coverage}>
      <div className={styles.coverageText}>
        <span className={styles.coverageHeadline}>
          {t("coverage.builtFrom", { ok: counts.ok, total: meta.tracked_queries })}
          {coverage != null && t("coverage.covering", { coverage })}
        </span>
        {notes.length > 0 && <span className={styles.muted}>{notes.join(" · ")}.</span>}
        <span className={styles.muted}>
          {meta.last_captured_at
            ? t("coverage.plansLastRead", { when: formatRelativeTime(meta.last_captured_at) })
            : t("coverage.noPlansYet")}{" "}
          {t("coverage.trackedExplained")}
        </span>
      </div>
      {coverage != null && (
        <div
          className={styles.meter}
          role="meter"
          aria-label={t("coverage.meterLabel")}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={coverage}
          style={{ "--coverage": `${coverage}%` }}
        >
          <span className={styles.meterFill} />
        </div>
      )}
    </div>
  );
}

function EdgeSample({ kind }) {
  return (
    <svg width="28" height="10" aria-hidden="true" className={styles.edgeSample}>
      <line x1="1" y1="5" x2="27" y2="5" className={styles[`sample_${kind}`]} />
    </svg>
  );
}

function Legend({ visibleKinds, onToggleKind, colorBy, counts }) {
  const { t } = useTranslation("workloadMap");
  return (
    <div className={styles.legend}>
      <fieldset className={styles.legendGroup}>
        <legend className={styles.legendTitle}>{t("legend.relationships")}</legend>
        {EDGE_KIND_KEYS.map((kind) => (
          <Checkbox key={kind} checked={visibleKinds.has(kind)} onChange={() => onToggleKind(kind)}>
            <EdgeSample kind={kind} />
            {EDGE_KINDS[kind].label} ({counts[kind]})
          </Checkbox>
        ))}
      </fieldset>
      <div className={styles.legendGroup}>
        <span className={styles.legendTitle}>{t("legend.nodes")}</span>
        <span className={styles.muted}>
          {t("legend.nodesExplained", { description: COLOR_MODE_DESCRIPTIONS[colorBy] })}
        </span>
        {colorBy === "health" && (
          <span className={styles.swatches}>
            <span className={`${styles.swatch} ${styles.swatchCritical}`} aria-hidden="true" />{" "}
            {t("common:severity.critical")}
            <span className={`${styles.swatch} ${styles.swatchAttention}`} aria-hidden="true" />{" "}
            {t("common:severity.attention")}
            <span className={`${styles.swatch} ${styles.swatchHealthy}`} aria-hidden="true" />{" "}
            {t("severityLabels.none")}
          </span>
        )}
        {colorBy !== "health" && (
          <span className={styles.swatches}>
            {t("legend.low")} <span className={`${styles.heatScale} ${styles[`heat_${colorBy}`]}`} aria-hidden="true" />{" "}
            {t("legend.high")}
          </span>
        )}
      </div>
    </div>
  );
}

// "How did the map look last week?": slides the window's end back in time,
// up to the store's 30-day retention. Left is the past, right is now. The
// URL only changes when the slider is released (or on keyboard input), so
// dragging doesn't fire a request per step.
function TimeSlider({ hours, until, onCommit }) {
  const { t } = useTranslation("workloadMap");
  const [now] = useState(() => Date.now());
  const maxBack = MAX_HISTORY_HOURS - hours;
  const committed = until ? Math.min(maxBack, Math.max(0, Math.round((now - until.getTime()) / 3_600_000))) : 0;
  const [draft, setDraft] = useState(null);
  const hoursAgo = draft ?? committed;
  const date =
    hoursAgo === 0
      ? null
      : new Date(now - hoursAgo * 3_600_000).toLocaleString(i18n.language, {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        });
  const label =
    hoursAgo === 0
      ? t("slider.now")
      : hoursAgo >= 48
        ? t("slider.daysAgo", { count: Math.round(hoursAgo / 24), date })
        : t("slider.hoursAgo", { count: hoursAgo, date });

  const commit = () => {
    if (draft == null) return;
    onCommit(draft === 0 ? null : new Date(now - draft * 3_600_000).toISOString());
    setDraft(null);
  };

  return (
    <div className={styles.timeSlider}>
      <label htmlFor="workload-until" className={styles.sliderLabel}>
        {t("slider.windowEnds")} <span className={styles.sliderValue}>{label}</span>
      </label>
      <input
        id="workload-until"
        type="range"
        min={0}
        max={maxBack}
        step={1}
        value={maxBack - hoursAgo}
        aria-valuetext={t("slider.valueText", { when: label })}
        onChange={(e) => setDraft(maxBack - Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        onBlur={commit}
      />
    </div>
  );
}

// Full screen: the map (with its toolbar and side panel) fills the window,
// and the browser goes full screen too when it allows it. Esc, the button,
// or leaving browser full screen returns to the normal page.
function useFullScreen() {
  const [on, setOn] = useState(false);
  const usedBrowserFullScreen = useRef(false);

  const enter = () => {
    setOn(true);
    // Must be called from the click itself; a refusal (iframe, policy)
    // just leaves the in-page full screen.
    const request = document.documentElement.requestFullscreen?.();
    if (request) {
      request.then(() => (usedBrowserFullScreen.current = true)).catch(() => {});
    }
  };

  const exit = () => {
    setOn(false);
    if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
    usedBrowserFullScreen.current = false;
  };

  useEffect(() => {
    if (!on) return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (event) => {
      if (event.key === "Escape" && !event.defaultPrevented) setOn(false);
    };
    // Esc in browser full screen never reaches the page as a keydown; the
    // browser leaves full screen, and this follows it.
    const onFullScreenChange = () => {
      if (!document.fullscreenElement && usedBrowserFullScreen.current) {
        usedBrowserFullScreen.current = false;
        setOn(false);
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("fullscreenchange", onFullScreenChange);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("fullscreenchange", onFullScreenChange);
    };
  }, [on]);

  // Leaving the screen while in full screen: give the browser window back.
  useEffect(
    () => () => {
      if (usedBrowserFullScreen.current && document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
    },
    []
  );

  return { on, enter, exit };
}

export default function WorkloadMapScreen({ targetId }) {
  const { t } = useTranslation("workloadMap");
  const [searchParams, setSearchParams] = useSearchParams();
  const { hours, until, view: requestedView, colorBy, visibleKinds, selection } = useMapParams(searchParams);
  // The 3D map is behind a feature flag (APP_FEATURE_WORKLOAD_3D in .env):
  // while it's off — or not known yet — the tab is hidden and a ?view=3d
  // link shows the 2D map.
  const featuresQuery = useQuery({
    queryKey: ["features"],
    queryFn: ({ signal }) => api.getFeatures({ signal }),
    staleTime: Infinity,
  });
  const has3d = featuresQuery.data?.workload_map_3d === true;
  const view = requestedView === "3d" && !has3d ? "map" : requestedView;
  const viewItems = (has3d ? VIEW_ITEMS : VIEW_ITEMS.filter((item) => item.key !== "3d")).map((item) => ({
    ...item,
    label: t(`views.${item.key}`),
  }));
  const rangeOptions = RANGE_OPTIONS.map((option) => ({ ...option, label: t(`ranges.${option.value}`) }));
  const [search, setSearch] = useState("");
  const [searchMiss, setSearchMiss] = useState(null);
  const fullScreen = useFullScreen();
  const untilIso = until ? until.toISOString() : null;

  const graphQuery = useQuery({
    queryKey: targetKey(targetId, "workload-graph", hours, ...(untilIso ? [untilIso] : [])),
    queryFn: ({ signal }) => api.getWorkloadGraph(targetId, hours, { signal }, untilIso),
    placeholderData: keepPreviousData,
  });
  const graph = graphQuery.data;

  const visibleEdges = useMemo(
    () => (graph ? graph.edges.filter((e) => visibleKinds.has(e.kind)) : []),
    [graph, visibleKinds]
  );

  const updateParams = (changes) => {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(changes)) {
      if (value == null || value === "") next.delete(key);
      else next.set(key, String(value));
    }
    setSearchParams(next, { replace: true });
  };

  const select = (next) =>
    updateParams({
      table: next?.type === "table" ? next.id : null,
      edge: next?.type === "edge" ? next.id : null,
    });

  const toggleKind = (kind) => {
    const hidden = EDGE_KIND_KEYS.filter((k) => (k === kind ? visibleKinds.has(k) : !visibleKinds.has(k)));
    updateParams({ hide: hidden.join(",") });
  };

  const handleSearch = (event) => {
    event.preventDefault();
    const term = search.trim().toLowerCase();
    if (!term || !graph) return;
    const exact = graph.nodes.find((n) => n.id.toLowerCase() === term);
    const byName = graph.nodes.filter((n) => n.table.toLowerCase() === term);
    const partial = graph.nodes.filter((n) => n.id.toLowerCase().includes(term));
    const match = exact ?? (byName.length === 1 ? byName[0] : partial.length === 1 ? partial[0] : null);
    if (match) {
      setSearchMiss(null);
      select({ type: "table", id: match.id });
    } else {
      // Kept as data and worded at render, so it follows a language switch.
      setSearchMiss({ count: partial.length, term: search.trim() });
    }
  };

  const toolbar = (
    <div className={styles.toolbar}>
      <SegmentedControl
        label={t("toolbar.timeRange")}
        options={rangeOptions}
        value={hours}
        onChange={(value) => updateParams({ hours: value === DEFAULT_HOURS ? null : value })}
      />
      <Tabs
        label={t("toolbar.view")}
        idPrefix="workload-view"
        items={viewItems}
        value={view}
        onChange={(key) => updateParams({ view: key === "map" ? null : key })}
      />
      {view !== "list" && (
        <SegmentedControl
          label={t("toolbar.colorBy")}
          size="sm"
          options={COLOR_MODES}
          value={colorBy}
          onChange={(value) => updateParams({ color: value === "health" ? null : value })}
        />
      )}
      <form className={styles.search} onSubmit={handleSearch} role="search">
        <label htmlFor="workload-search" className="sr-only">
          {t("search.label")}
        </label>
        <input
          id="workload-search"
          type="search"
          list="workload-tables"
          placeholder={t("search.placeholder")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <datalist id="workload-tables">
          {graph?.nodes.map((n) => (
            <option key={n.id} value={n.id} />
          ))}
        </datalist>
        <button type="submit" className="button-secondary button-sm">
          <Search size={13} aria-hidden="true" />
          {t("common:actions.find")}
        </button>
      </form>
      <button
        type="button"
        className="button-secondary button-sm"
        onClick={fullScreen.on ? fullScreen.exit : fullScreen.enter}
        aria-pressed={fullScreen.on}
        title={fullScreen.on ? t("toolbar.exitFullScreenTitle") : t("toolbar.fullScreen")}
      >
        {fullScreen.on ? <Minimize2 size={13} aria-hidden="true" /> : <Maximize2 size={13} aria-hidden="true" />}
        {fullScreen.on ? t("toolbar.exitFullScreen") : t("toolbar.fullScreen")}
      </button>
    </div>
  );

  if (graphQuery.isPending) {
    return (
      <Card>
        {toolbar}
        <Skeleton height={480} label={t("screen.building")} />
      </Card>
    );
  }

  if (graphQuery.error && !graph) {
    return (
      <Card>
        {toolbar}
        <Alert tone="critical">{graphQuery.error.message}</Alert>
      </Card>
    );
  }

  const { meta } = graph;
  const empty = graph.nodes.length === 0;
  const emptyMessage =
    meta.tracked_queries === 0
      ? t("screen.emptyNoQueries")
      : meta.query_status_counts.ok === 0
        ? t("screen.emptyNonePlanned")
        : t("screen.emptyNoUserTables");

  return (
    <div
      className={`${styles.screen} ${fullScreen.on ? styles.fullscreen : ""}`}
      role={fullScreen.on ? "dialog" : undefined}
      aria-modal={fullScreen.on ? "true" : undefined}
      aria-label={fullScreen.on ? t("screen.fullScreenLabel") : undefined}
    >
      <Card className={styles.controlsCard}>
        {toolbar}
        {searchMiss && (
          <Alert tone="info" compact>
            {searchMiss.count > 1
              ? t("search.manyMatches", { count: searchMiss.count, term: searchMiss.term })
              : t("search.noMatch", { term: searchMiss.term })}
          </Alert>
        )}
        {graphQuery.error && <Alert tone="critical">{graphQuery.error.message}</Alert>}
        <TimeSlider
          key={untilIso ?? "now"}
          hours={hours}
          until={until}
          onCommit={(iso) => updateParams({ until: iso })}
        />
        {until && (
          <Alert tone="info" compact>
            {t("screen.showingPast", { range: t(`rangeLabels.${hours}`), until: until.toLocaleString() })}
          </Alert>
        )}
        <div className={styles.coverageBlock}>
          <CoverageMeter meta={meta} />
        </div>
        {meta.weight_basis === "lifetime" && (
          <Alert tone="info" compact>
            {t("screen.lifetimeWeights", { range: t(`rangeLabelsLast.${hours}`) })}
          </Alert>
        )}
        {meta.truncated_nodes > 0 && (
          <Alert tone="attention" compact>
            {t("screen.truncated", { count: graph.nodes.length, more: meta.truncated_nodes })}
          </Alert>
        )}
      </Card>

      {empty ? (
        <Card>
          <EmptyState icon={Network} title={t("screen.emptyTitle")}>
            {emptyMessage}
          </EmptyState>
        </Card>
      ) : (
        <div className={`${styles.body} ${selection ? styles.withPanel : ""}`}>
          <TabPanel idPrefix="workload-view" value={view} className={styles.main}>
            {view === "map" || view === "3d" ? (
              <Card flush className={styles.graphCard}>
                {view === "map" ? (
                  <WorkloadGraph
                    nodes={graph.nodes}
                    edges={graph.edges}
                    colorBy={colorBy}
                    visibleKinds={visibleKinds}
                    selection={selection}
                    onSelect={select}
                    label={t("screen.graphLabel", { tables: graph.nodes.length, edges: graph.edges.length })}
                  />
                ) : (
                  <Suspense fallback={<Skeleton height={480} label={t("screen.loading3d")} />}>
                    <WorkloadGraph3D
                      nodes={graph.nodes}
                      edges={graph.edges}
                      colorBy={colorBy}
                      visibleKinds={visibleKinds}
                      selection={selection}
                      onSelect={select}
                      label={t("screen.graph3dLabel", { tables: graph.nodes.length })}
                    />
                  </Suspense>
                )}
                {view === "3d" && <p className={styles.note}>{t("screen.note3d")}</p>}
                <Legend
                  visibleKinds={visibleKinds}
                  onToggleKind={toggleKind}
                  colorBy={colorBy}
                  counts={meta.edge_counts}
                />
              </Card>
            ) : (
              <Card className={styles.listCard}>
                <Legend
                  visibleKinds={visibleKinds}
                  onToggleKind={toggleKind}
                  colorBy={colorBy}
                  counts={meta.edge_counts}
                />
                <WorkloadRelationsTable
                  edges={visibleEdges}
                  selectedId={selection?.type === "edge" ? selection.id : null}
                  onSelect={select}
                />
              </Card>
            )}
          </TabPanel>
          {selection && (
            <WorkloadSidePanel
              targetId={targetId}
              hours={hours}
              until={untilIso}
              graph={graph}
              selection={selection}
              onSelect={select}
              onClose={() => select(null)}
            />
          )}
        </div>
      )}
    </div>
  );
}
