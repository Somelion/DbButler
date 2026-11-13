import { useEffect, useMemo, useRef, useState } from "react";
import cytoscape from "cytoscape";
import fcose from "cytoscape-fcose";
import { Maximize, ZoomIn, ZoomOut } from "lucide-react";
import styles from "./WorkloadGraph.module.css";
import { readColorTokens, readToken, useThemeSnapshot } from "./cssColors";
import { COLOR_TOKENS, nodeColors } from "./workloadColors";
import { EDGE_KINDS, isUnindexedFkEdge } from "./workloadKinds";

cytoscape.use(fcose);

const MIN_NODE_SIZE = 22;
const MAX_NODE_SIZE = 72;
const MIN_EDGE_WIDTH = 1.5;
const MAX_EDGE_WIDTH = 10;

const schemaNodeId = (schema) => `schema:${schema}`;

// Node size ~ sqrt(bytes) relative to the largest table, edge width ~
// sqrt(DB-time share) relative to the heaviest edge: area-proportional
// rather than radius-proportional, so one huge table doesn't flatten the
// rest into dots.
function buildElements(nodes, edges) {
  const maxBytes = Math.max(1, ...nodes.map((n) => n.total_bytes ?? 0));
  const maxShare = Math.max(0.0001, ...edges.map((e) => e.share_pct));
  const schemas = [...new Set(nodes.map((n) => n.schema))];
  const grouped = schemas.length > 1;

  const elements = [];
  if (grouped) {
    for (const schema of schemas) {
      // Not selectable: a click on the box must not steal the selection
      // from the table the URL and side panel show.
      elements.push({
        group: "nodes",
        data: { id: schemaNodeId(schema), label: schema, kind: "schema" },
        selectable: false,
      });
    }
  }
  for (const node of nodes) {
    const size = MIN_NODE_SIZE + (MAX_NODE_SIZE - MIN_NODE_SIZE) * Math.sqrt((node.total_bytes ?? 0) / maxBytes);
    elements.push({
      group: "nodes",
      data: {
        id: node.id,
        label: grouped ? node.table : node.id.startsWith("public.") ? node.table : node.id,
        kind: "table",
        parent: grouped ? schemaNodeId(node.schema) : undefined,
        size,
        faded: !node.in_workload,
      },
    });
  }
  for (const edge of edges) {
    const width =
      edge.kind === "fk_unused"
        ? MIN_EDGE_WIDTH
        : MIN_EDGE_WIDTH + (MAX_EDGE_WIDTH - MIN_EDGE_WIDTH) * Math.sqrt(edge.share_pct / maxShare);
    elements.push({
      group: "edges",
      data: {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        kind: edge.kind,
        width,
        unindexed: isUnindexedFkEdge(edge),
      },
    });
  }
  return elements;
}

function buildStylesheet(palette, fontFamily) {
  return [
    {
      selector: "node[kind = 'table']",
      style: {
        "background-color": "data(color)",
        width: "data(size)",
        height: "data(size)",
        label: "data(label)",
        "font-family": fontFamily,
        "font-size": 11,
        color: palette.text,
        "text-valign": "bottom",
        "text-halign": "center",
        "text-margin-y": 4,
        "text-outline-color": palette.bg,
        "text-outline-width": 2,
        "border-width": 1.5,
        "border-color": palette.card,
      },
    },
    { selector: "node[?faded]", style: { opacity: 0.55 } },
    {
      selector: "node[kind = 'schema']",
      style: {
        shape: "round-rectangle",
        "background-color": palette["row-bg"],
        "background-opacity": 0.7,
        "border-width": 1,
        "border-color": palette.border,
        label: "data(label)",
        "font-family": fontFamily,
        "font-size": 12,
        "font-weight": 600,
        color: palette["text-muted"],
        "text-valign": "top",
        "text-halign": "center",
        "text-margin-y": -4,
        padding: 18,
      },
    },
    {
      selector: "edge",
      style: {
        width: "data(width)",
        "curve-style": "bezier",
        "line-color": palette["text-secondary"],
        opacity: 0.8,
      },
    },
    {
      selector: "edge[kind = 'no_fk']",
      style: { "line-style": "dashed", "line-dash-pattern": [8, 5], "line-color": palette["attention-solid"] },
    },
    {
      selector: "edge[kind = 'fk_unused']",
      style: { "line-style": "dotted", "line-color": palette["unknown-solid"] },
    },
    {
      selector: "edge[?unindexed]",
      style: {
        label: "unindexed FK",
        "font-family": fontFamily,
        "font-size": 9,
        color: palette["attention-text"],
        "text-rotation": "autorotate",
        "text-background-color": palette.bg,
        "text-background-opacity": 0.85,
        "text-background-padding": 2,
      },
    },
    { selector: ".hidden", style: { display: "none" } },
    { selector: ".dimmed", style: { opacity: 0.12 } },
    { selector: "node:selected", style: { "border-width": 3, "border-color": palette.accent } },
    { selector: "edge:selected", style: { "line-color": palette.accent, opacity: 1 } },
  ];
}

const LAYOUT = {
  name: "fcose",
  quality: "default",
  randomize: true,
  animate: false,
  nodeDimensionsIncludeLabels: true,
  idealEdgeLength: 110,
  nodeRepulsion: 9000,
  nodeSeparation: 90,
  packComponents: true,
  padding: 30,
};

// The cytoscape canvas. A canvas can't be read by screen readers or driven
// by keyboard: the screen pairs it with a search box and the Relationships
// table, which hold the same information. `selection` is
// { type: "table" | "edge", id } or null.
export default function WorkloadGraph({ nodes, edges, colorBy, visibleKinds, selection, onSelect, label }) {
  const containerRef = useRef(null);
  const cyRef = useRef(null);
  const onSelectRef = useRef(onSelect);
  const [hover, setHover] = useState(null);
  const themeSnapshot = useThemeSnapshot();
  const elements = useMemo(() => buildElements(nodes, edges), [nodes, edges]);
  const edgeById = useMemo(() => new Map(edges.map((e) => [e.id, e])), [edges]);
  const edgeByIdRef = useRef(edgeById);
  const appearanceRef = useRef({ nodes, colorBy });

  // Declared first, so the effects below always see the current props.
  useEffect(() => {
    onSelectRef.current = onSelect;
    edgeByIdRef.current = edgeById;
    appearanceRef.current = { nodes, colorBy };
  });

  useEffect(() => {
    const cy = cytoscape({
      container: containerRef.current,
      boxSelectionEnabled: false,
      minZoom: 0.15,
      maxZoom: 3,
      // The real sizes and labels must be in place before the first layout,
      // or fcose lays out default 30px unlabelled dots.
      style: buildStylesheet(readColorTokens(COLOR_TOKENS), readToken("font-sans")),
    });
    cy.on("tap", "node[kind = 'table']", (event) => onSelectRef.current({ type: "table", id: event.target.id() }));
    cy.on("tap", "edge", (event) => onSelectRef.current({ type: "edge", id: event.target.id() }));
    cy.on("tap", (event) => {
      if (event.target === cy) onSelectRef.current(null);
    });
    cy.on("mouseover", "edge", (event) => {
      const edge = edgeByIdRef.current.get(event.target.id());
      if (edge) setHover({ edge, x: event.renderedPosition.x, y: event.renderedPosition.y });
    });
    cy.on("mouseout", "edge", () => setHover(null));
    cyRef.current = cy;
    // Cytoscape only notices window resizes on its own; the container also
    // changes size when the map goes full screen or the side panel opens.
    const observer = new ResizeObserver(() => cy.resize());
    observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
      cy.destroy();
      cyRef.current = null;
    };
  }, []);

  // New data: replace the elements (with their colours already set, so the
  // data(color) mapping never sees a node without one) and lay them out.
  useEffect(() => {
    const cy = cyRef.current;
    const { nodes: currentNodes, colorBy: currentColorBy } = appearanceRef.current;
    const colors = nodeColors(currentNodes, currentColorBy, readColorTokens(COLOR_TOKENS));
    cy.batch(() => {
      cy.elements().remove();
      cy.add(
        elements.map((element) =>
          colors.has(element.data.id) ? { ...element, data: { ...element.data, color: colors.get(element.data.id) } } : element
        )
      );
    });
    if (elements.length > 0) {
      cy.layout(LAYOUT).run();
      cy.fit(undefined, 30);
    }
  }, [elements]);

  // Colours: on theme or colouring-mode change (and after new data, which is
  // harmless — the same colours again).
  useEffect(() => {
    const cy = cyRef.current;
    const palette = readColorTokens(COLOR_TOKENS);
    const colors = nodeColors(nodes, colorBy, palette);
    cy.style(buildStylesheet(palette, readToken("font-sans")));
    cy.batch(() => {
      for (const [id, color] of colors) cy.getElementById(id).data("color", color);
    });
  }, [nodes, colorBy, themeSnapshot, elements]);

  useEffect(() => {
    const cy = cyRef.current;
    cy.batch(() => {
      cy.edges().forEach((edge) => {
        edge.toggleClass("hidden", !visibleKinds.has(edge.data("kind")));
      });
    });
  }, [visibleKinds, elements]);

  // Selection: highlight it and its neighbourhood, dim the rest, and bring
  // it into view.
  useEffect(() => {
    const cy = cyRef.current;
    cy.batch(() => {
      cy.elements().unselect().removeClass("dimmed");
      if (!selection) return;
      const element = cy.getElementById(selection.id);
      if (element.empty()) return;
      element.select();
      const focus = element.isNode() ? element.closedNeighborhood() : element.connectedNodes().union(element);
      cy.elements("node[kind = 'table'], edge").not(focus).addClass("dimmed");
    });
    if (!selection) return;
    const element = cy.getElementById(selection.id);
    if (element.empty()) return;
    const extent = cy.extent();
    const box = element.boundingBox();
    const visible = box.x1 >= extent.x1 && box.x2 <= extent.x2 && box.y1 >= extent.y1 && box.y2 <= extent.y2;
    if (!visible) cy.center(element);
  }, [selection, elements]);

  const zoomBy = (factor) => {
    const cy = cyRef.current;
    cy.zoom({
      level: cy.zoom() * factor,
      renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 },
    });
  };

  return (
    <div className={styles.wrap}>
      <div ref={containerRef} className={styles.canvas} role="img" aria-label={label} />
      <div className={styles.controls}>
        <button type="button" className="button-icon" onClick={() => zoomBy(1.25)} aria-label="Zoom in">
          <ZoomIn size={16} aria-hidden="true" />
        </button>
        <button type="button" className="button-icon" onClick={() => zoomBy(0.8)} aria-label="Zoom out">
          <ZoomOut size={16} aria-hidden="true" />
        </button>
        <button
          type="button"
          className="button-icon"
          onClick={() => cyRef.current.fit(undefined, 30)}
          aria-label="Fit the whole map"
        >
          <Maximize size={16} aria-hidden="true" />
        </button>
      </div>
      {hover && (
        <div
          className={styles.tooltip}
          style={{ "--tooltip-x": `${hover.x}px`, "--tooltip-y": `${hover.y}px` }}
          aria-hidden="true"
        >
          <span className={styles.tooltipTitle}>
            {hover.edge.source} ⇄ {hover.edge.target}
          </span>
          <span>{EDGE_KINDS[hover.edge.kind].label}</span>
          {hover.edge.column_pairs.length > 0 && (
            <span className={styles.tooltipMono}>
              {hover.edge.column_pairs.map(([a, b]) => `${a} = ${b}`).join(", ")}
            </span>
          )}
          {hover.edge.kind !== "fk_unused" && (
            <span>
              {hover.edge.share_pct}% of DB time · {hover.edge.query_count} quer
              {hover.edge.query_count === 1 ? "y" : "ies"}
              {hover.edge.join_strategies.length > 0 && ` · ${hover.edge.join_strategies.join(", ")}`}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
