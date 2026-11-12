import { mixRgb } from "./cssColors";
import { HEAT_METRIC } from "./workloadKinds";

// Design tokens the Workload Map's canvas renderers (2D cytoscape, 3D
// three.js) resolve to rgb — see cssColors.js.
export const COLOR_TOKENS = [
  "bg",
  "card",
  "row-bg",
  "border",
  "text",
  "text-secondary",
  "text-muted",
  "accent",
  "critical-solid",
  "attention-solid",
  "attention-text",
  "healthy-solid",
  "unknown-solid",
  "info-solid",
  "chart-4",
];

const SEVERITY_TOKEN = {
  critical: "critical-solid",
  attention: "attention-solid",
  healthy: "healthy-solid",
  unknown: "unknown-solid",
};

// Fill colour per node for the active colouring mode: worst open finding,
// or a cold→hot blend by seq-scan or write volume.
export function nodeColors(nodes, colorBy, palette) {
  const colors = new Map();
  if (colorBy === "health") {
    for (const node of nodes) {
      const token = SEVERITY_TOKEN[node.severity] ?? "healthy-solid";
      colors.set(node.id, palette[token]);
    }
    return colors;
  }
  const metric = HEAT_METRIC[colorBy];
  const max = Math.max(0, ...nodes.map((n) => n[metric] ?? 0));
  const hot = colorBy === "seq" ? palette["critical-solid"] : palette["chart-4"];
  const cold = mixRgb(palette["unknown-solid"], palette.card, 0.35);
  for (const node of nodes) {
    const value = node[metric];
    colors.set(node.id, value == null || max === 0 ? cold : mixRgb(cold, hot, Math.sqrt(value / max)));
  }
  return colors;
}

export function edgeColor(kind, palette) {
  if (kind === "no_fk") return palette["attention-solid"];
  if (kind === "fk_unused") return palette["unknown-solid"];
  return palette["text-secondary"];
}
