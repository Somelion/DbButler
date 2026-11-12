import { lazy, Suspense, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { Network, Search } from "lucide-react";
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

const RANGE_OPTIONS = [
  { value: 24, label: "24h" },
  { value: 168, label: "7d" },
  { value: 720, label: "30d" },
];
const DEFAULT_HOURS = 168;
const VIEW_ITEMS = [
  { key: "map", label: "Map" },
  { key: "3d", label: "3D" },
  { key: "list", label: "Relationships" },
];
const RANGE_LABEL = { 24: "24 hours", 168: "7 days", 720: "30 days" };
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
  const counts = meta.query_status_counts;
  const coverage = meta.coverage_pct;
  const notes = [];
  if (counts.needs_generic_plan > 0) {
    notes.push(
      `${counts.needs_generic_plan} parameterized quer${counts.needs_generic_plan === 1 ? "y needs" : "ies need"} PostgreSQL 16+ to be planned`
    );
  }
  if (counts.explain_failed > 0) {
    notes.push(`${counts.explain_failed} couldn't be planned (utility statements such as SET or COMMIT, mostly)`);
  }
  if (counts.not_planned_yet > 0) notes.push(`${counts.not_planned_yet} not planned yet`);

  return (
    <div className={styles.coverage}>
      <div className={styles.coverageText}>
        <span className={styles.coverageHeadline}>
          Built from {counts.ok} of {meta.tracked_queries} tracked queries
          {coverage != null && <>, covering {coverage}% of their DB time</>}
        </span>
        {notes.length > 0 && <span className={styles.muted}>{notes.join(" · ")}.</span>}
        <span className={styles.muted}>
          {meta.last_captured_at
            ? `Plans last read ${formatRelativeTime(meta.last_captured_at)}.`
            : "No plans read yet."}{" "}
          Tracked queries are the top 50 by total time in pg_stat_statements.
        </span>
      </div>
      {coverage != null && (
        <div
          className={styles.meter}
          role="meter"
          aria-label="Share of tracked DB time the map covers"
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
  return (
    <div className={styles.legend}>
      <fieldset className={styles.legendGroup}>
        <legend className={styles.legendTitle}>Relationships</legend>
        {EDGE_KIND_KEYS.map((kind) => (
          <Checkbox key={kind} checked={visibleKinds.has(kind)} onChange={() => onToggleKind(kind)}>
            <EdgeSample kind={kind} />
            {EDGE_KINDS[kind].label} ({counts[kind]})
          </Checkbox>
        ))}
      </fieldset>
      <div className={styles.legendGroup}>
        <span className={styles.legendTitle}>Nodes</span>
        <span className={styles.muted}>
          Size: table size. Colour: {COLOR_MODE_DESCRIPTIONS[colorBy]} Faded: only on the map through a foreign key.
        </span>
        {colorBy === "health" && (
          <span className={styles.swatches}>
            <span className={`${styles.swatch} ${styles.swatchCritical}`} aria-hidden="true" /> Critical
            <span className={`${styles.swatch} ${styles.swatchAttention}`} aria-hidden="true" /> Attention
            <span className={`${styles.swatch} ${styles.swatchHealthy}`} aria-hidden="true" /> No open findings
          </span>
        )}
        {colorBy !== "health" && (
          <span className={styles.swatches}>
            low <span className={`${styles.heatScale} ${styles[`heat_${colorBy}`]}`} aria-hidden="true" /> high
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
  const [now] = useState(() => Date.now());
  const maxBack = MAX_HISTORY_HOURS - hours;
  const committed = until ? Math.min(maxBack, Math.max(0, Math.round((now - until.getTime()) / 3_600_000))) : 0;
  const [draft, setDraft] = useState(null);
  const hoursAgo = draft ?? committed;
  const label =
    hoursAgo === 0
      ? "now"
      : `${hoursAgo >= 48 ? `${Math.round(hoursAgo / 24)} days` : `${hoursAgo} hours`} ago (${new Date(
          now - hoursAgo * 3_600_000
        ).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })})`;

  const commit = () => {
    if (draft == null) return;
    onCommit(draft === 0 ? null : new Date(now - draft * 3_600_000).toISOString());
    setDraft(null);
  };

  return (
    <div className={styles.timeSlider}>
      <label htmlFor="workload-until" className={styles.sliderLabel}>
        Window ends: <span className={styles.sliderValue}>{label}</span>
      </label>
      <input
        id="workload-until"
        type="range"
        min={0}
        max={maxBack}
        step={1}
        value={maxBack - hoursAgo}
        aria-valuetext={`Window ends ${label}`}
        onChange={(e) => setDraft(maxBack - Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        onBlur={commit}
      />
    </div>
  );
}

export default function WorkloadMapScreen({ targetId }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const { hours, until, view, colorBy, visibleKinds, selection } = useMapParams(searchParams);
  const [search, setSearch] = useState("");
  const [searchMiss, setSearchMiss] = useState(null);
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
      setSearchMiss(
        partial.length > 1 ? `${partial.length} tables match "${search.trim()}" — type more of the name.` : `No table "${search.trim()}" on the map.`
      );
    }
  };

  const toolbar = (
    <div className={styles.toolbar}>
      <SegmentedControl
        label="Time range"
        options={RANGE_OPTIONS}
        value={hours}
        onChange={(value) => updateParams({ hours: value === DEFAULT_HOURS ? null : value })}
      />
      <Tabs
        label="View"
        idPrefix="workload-view"
        items={VIEW_ITEMS}
        value={view}
        onChange={(key) => updateParams({ view: key === "map" ? null : key })}
      />
      {view !== "list" && (
        <SegmentedControl
          label="Colour tables by"
          size="sm"
          options={COLOR_MODES}
          value={colorBy}
          onChange={(value) => updateParams({ color: value === "health" ? null : value })}
        />
      )}
      <form className={styles.search} onSubmit={handleSearch} role="search">
        <label htmlFor="workload-search" className="sr-only">
          Find a table on the map
        </label>
        <input
          id="workload-search"
          type="search"
          list="workload-tables"
          placeholder="Find a table…"
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
          Find
        </button>
      </form>
    </div>
  );

  if (graphQuery.isPending) {
    return (
      <Card>
        {toolbar}
        <Skeleton height={480} label="Building the workload map…" />
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
      ? "No queries are tracked yet. The map is built from the top 50 queries in pg_stat_statements, which DbButler starts tracking within a few minutes of connecting."
      : meta.query_status_counts.ok === 0
        ? "None of the tracked queries has been planned yet. Their plans are read every 5 minutes (the plan regression job on the Settings screen) — check back shortly."
        : "No user tables to show: the tracked queries only touch system catalogs, or tables outside this connection's schema filter.";

  return (
    <div className={styles.screen}>
      <Card>
        {toolbar}
        {searchMiss && (
          <Alert tone="info" compact>
            {searchMiss}
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
            Showing the {RANGE_LABEL[hours]} up to {until.toLocaleString()}: the queries tracked then, weighted by the
            time they took in that window. Join columns come from each query's current plan.
          </Alert>
        )}
        <CoverageMeter meta={meta} />
        {meta.weight_basis === "lifetime" && (
          <Alert tone="info" compact>
            No tracked query ran in the last {RANGE_LABEL[hours]}, so weights use each query's total since
            pg_stat_statements was last reset.
          </Alert>
        )}
        {meta.truncated_nodes > 0 && (
          <Alert tone="attention" compact>
            Showing the {graph.nodes.length} tables carrying the most DB time; {meta.truncated_nodes} more are left
            off.
          </Alert>
        )}
      </Card>

      {empty ? (
        <Card>
          <EmptyState icon={Network} title="Nothing to map yet">
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
                    label={`Workload map: ${graph.nodes.length} tables and ${graph.edges.length} relationships. The Relationships view lists the same information as a table.`}
                  />
                ) : (
                  <Suspense fallback={<Skeleton height={480} label="Loading the 3D view…" />}>
                    <WorkloadGraph3D
                      nodes={graph.nodes}
                      edges={graph.edges}
                      colorBy={colorBy}
                      visibleKinds={visibleKinds}
                      selection={selection}
                      onSelect={select}
                      label={`3D workload map: ${graph.nodes.length} tables, higher means more DB time. Drag to rotate, scroll to zoom. The Relationships view lists the same information as a table.`}
                    />
                  </Suspense>
                )}
                {view === "3d" && (
                  <p className={styles.note}>
                    3D: height is the table's share of DB time, each schema is its own cluster, and relationships are
                    told apart by colour only. Drag to rotate, scroll to zoom.
                  </p>
                )}
                <Legend
                  visibleKinds={visibleKinds}
                  onToggleKind={toggleKind}
                  colorBy={colorBy}
                  counts={meta.edge_counts}
                />
              </Card>
            ) : (
              <Card>
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
