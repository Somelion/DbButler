import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import ForceGraph3D from "react-force-graph-3d";
import { Maximize } from "lucide-react";
import styles from "./WorkloadGraph.module.css";
import { readColorTokens, useThemeSnapshot } from "./cssColors";
import { Alert } from "./components";
import { COLOR_TOKENS, edgeColor, nodeColors } from "./workloadColors";
import { EDGE_KINDS } from "./workloadKinds";
import i18n from "./i18n";

// Height (y) a node floats to: the busiest table at the top.
const MAX_HEIGHT = 220;
const SCHEMA_RADIUS = 260;
const SCHEMA_PULL = 0.08;

function webglAvailable() {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") || canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

// The tooltip is rendered as HTML by the library: table names are escaped.
const escapeHtml = (text) =>
  String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// Data-independent accessors live at module level: stable identities.
// Their text is translated when the library calls them (on hover).
const nodeLabel = (n) => i18n.t("workloadMap:graph.nodeLabel", { name: escapeHtml(n.id), share: n.share });
const linkWidth = (l) => (l.kind === "fk_unused" ? 0.4 : 0.6 + 3 * Math.sqrt(l.share / 100));
const linkLabel = (l) =>
  `${escapeHtml(l.source.id ?? l.source)} ⇄ ${escapeHtml(l.target.id ?? l.target)} — ${EDGE_KINDS[l.kind].label}`;

// "Galaxy" layout: each schema gets its own centre on a circle, and a weak
// force pulls its tables there, so schemas separate into clusters.
function schemaClusterForce(centres) {
  let nodes = [];
  const force = (alpha) => {
    for (const node of nodes) {
      const centre = centres.get(node.schema);
      if (!centre) continue;
      node.vx += (centre.x - node.x) * SCHEMA_PULL * alpha;
      node.vz += (centre.z - node.z) * SCHEMA_PULL * alpha;
    }
  };
  force.initialize = (all) => {
    nodes = all;
  };
  return force;
}

// The Workload Map in 3D (three.js via react-force-graph-3d, loaded only
// when this view is chosen). Height is query load — hot tables float up —
// and schemas form separate clusters. An exploration view: the 2D map and
// the Relationships table remain the precise, accessible ones.
export default function WorkloadGraph3D({ nodes, edges, colorBy, visibleKinds, selection, onSelect, label }) {
  const { t } = useTranslation("workloadMap");
  const wrapRef = useRef(null);
  const graphRef = useRef(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [supported] = useState(webglAvailable);
  const themeSnapshot = useThemeSnapshot();

  useEffect(() => {
    const element = wrapRef.current;
    if (!element) return undefined;
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: Math.floor(entry.contentRect.width), height: Math.floor(entry.contentRect.height) });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Theme-dependent colours. themeSnapshot is the dependency that matters:
  // the tokens it re-reads change with it.
  // eslint-disable-next-line react-hooks/exhaustive-deps -- re-resolve on theme change
  const palette = useMemo(() => readColorTokens(COLOR_TOKENS), [themeSnapshot]);

  const graphData = useMemo(() => {
    const maxShare = Math.max(0.0001, ...nodes.map((n) => n.share_pct));
    const maxBytes = Math.max(1, ...nodes.map((n) => n.total_bytes ?? 0));
    return {
      nodes: nodes.map((n) => ({
        id: n.id,
        schema: n.schema,
        name: n.table,
        val: 1 + 12 * Math.sqrt((n.total_bytes ?? 0) / maxBytes),
        // Fixed height; x/z stay free for the force layout.
        fy: MAX_HEIGHT * Math.sqrt(n.share_pct / maxShare) - MAX_HEIGHT / 2,
        faded: !n.in_workload,
        share: n.share_pct,
      })),
      links: edges.map((e) => ({ id: e.id, source: e.source, target: e.target, kind: e.kind, share: e.share_pct })),
    };
  }, [nodes, edges]);

  const colors = useMemo(() => nodeColors(nodes, colorBy, palette), [nodes, colorBy, palette]);

  // The graph only mounts once the container has a size, so the cluster
  // force is (re)attached when `ready` flips, not just when data changes.
  const ready = size.width > 0;
  useEffect(() => {
    const graph = graphRef.current;
    if (!ready || !graph) return;
    const schemas = [...new Set(nodes.map((n) => n.schema))];
    const centres = new Map(
      schemas.map((schema, i) => {
        const angle = (2 * Math.PI * i) / schemas.length;
        const radius = schemas.length > 1 ? SCHEMA_RADIUS : 0;
        return [schema, { x: Math.cos(angle) * radius, z: Math.sin(angle) * radius }];
      })
    );
    graph.d3Force("schema", schemaClusterForce(centres));
    graph.d3ReheatSimulation();
  }, [graphData, nodes, ready]);

  // The library restarts its engine on every prop update and calls
  // onEngineStop again — fit the camera once per dataset only, so a click
  // or a re-render never yanks the view back after the user rotated it.
  const fittedRef = useRef(null);
  const graphDataRef = useRef(graphData);
  const onSelectRef = useRef(onSelect);
  useEffect(() => {
    graphDataRef.current = graphData;
    onSelectRef.current = onSelect;
  });
  const handleEngineStop = useCallback(() => {
    if (fittedRef.current === graphDataRef.current) return;
    fittedRef.current = graphDataRef.current;
    graphRef.current?.zoomToFit(400, 40);
  }, []);
  const handleNodeClick = useCallback((n) => onSelectRef.current({ type: "table", id: n.id }), []);
  const handleLinkClick = useCallback((l) => onSelectRef.current({ type: "edge", id: l.id }), []);
  const handleBackgroundClick = useCallback(() => onSelectRef.current(null), []);

  // Accessors are memoised: each new function identity counts as a prop
  // update to the library.
  const selectedId = selection?.id;
  const neighbours = useMemo(() => {
    const ids = new Set();
    if (selection?.type === "table") {
      ids.add(selection.id);
      for (const e of edges) {
        if (e.source === selection.id) ids.add(e.target);
        if (e.target === selection.id) ids.add(e.source);
      }
    } else if (selection?.type === "edge") {
      const edge = edges.find((e) => e.id === selection.id);
      if (edge) {
        ids.add(edge.source);
        ids.add(edge.target);
      }
    }
    return ids;
  }, [selection, edges]);
  const nodeColor = useCallback(
    (n) => {
      if (n.id === selectedId) return palette.accent;
      const dimmed = neighbours.size > 0 && !neighbours.has(n.id);
      return dimmed || n.faded ? palette["unknown-solid"] : colors.get(n.id);
    },
    [selectedId, neighbours, palette, colors]
  );
  const linkColor = useCallback(
    (l) => (l.id === selectedId ? palette.accent : edgeColor(l.kind, palette)),
    [selectedId, palette]
  );
  const linkVisibility = useCallback((l) => visibleKinds.has(l.kind), [visibleKinds]);

  if (!supported) {
    return (
      <Alert tone="info">{t("graph.noWebgl")}</Alert>
    );
  }


  return (
    <div ref={wrapRef} className={styles.wrap} role="group" aria-label={label}>
      {size.width > 0 && (
        <ForceGraph3D
          ref={graphRef}
          graphData={graphData}
          width={size.width}
          height={size.height}
          backgroundColor={palette.bg}
          showNavInfo={false}
          nodeRelSize={4}
          nodeVal="val"
          nodeOpacity={0.92}
          nodeLabel={nodeLabel}
          nodeColor={nodeColor}
          linkVisibility={linkVisibility}
          linkColor={linkColor}
          linkWidth={linkWidth}
          linkOpacity={0.55}
          linkLabel={linkLabel}
          onNodeClick={handleNodeClick}
          onLinkClick={handleLinkClick}
          onBackgroundClick={handleBackgroundClick}
          enableNodeDrag={false}
          cooldownTime={6000}
          onEngineStop={handleEngineStop}
        />
      )}
      <div className={styles.controls}>
        <button
          type="button"
          className="button-icon"
          onClick={() => graphRef.current?.zoomToFit(400, 40)}
          aria-label={t("graph.fit")}
        >
          <Maximize size={16} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}
