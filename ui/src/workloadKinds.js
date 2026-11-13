import i18n from "./i18n";

// Shared vocabulary for the Workload Map's three edge kinds and its node
// colouring modes (graph, legend, relationships table, side panel).
// Labels and descriptions are getters, so they read in the current UI
// language (workloadMap:*) whenever a component renders them.

const edgeKind = (key, tone) => ({
  tone,
  get label() {
    return i18n.t(`workloadMap:edgeKinds.${key}.label`);
  },
  get description() {
    return i18n.t(`workloadMap:edgeKinds.${key}.description`);
  },
});

export const EDGE_KINDS = {
  fk: edgeKind("fk", "neutral"),
  no_fk: edgeKind("no_fk", "attention"),
  fk_unused: edgeKind("fk_unused", "unknown"),
};

export const EDGE_KIND_KEYS = Object.keys(EDGE_KINDS);

const colorMode = (value) => ({
  value,
  get label() {
    return i18n.t(`workloadMap:colorModes.${value}`);
  },
});

export const COLOR_MODES = [colorMode("health"), colorMode("seq"), colorMode("writes")];

export const COLOR_MODE_DESCRIPTIONS = {
  get health() {
    return i18n.t("workloadMap:colorModeDescriptions.health");
  },
  get seq() {
    return i18n.t("workloadMap:colorModeDescriptions.seq");
  },
  get writes() {
    return i18n.t("workloadMap:colorModeDescriptions.writes");
  },
};

// The node metric each heat mode scales by.
export const HEAT_METRIC = { seq: "seq_tup_read", writes: "writes" };

export const SEVERITY_LABELS = {
  get critical() {
    return i18n.t("workloadMap:severityLabels.critical");
  },
  get attention() {
    return i18n.t("workloadMap:severityLabels.attention");
  },
  get healthy() {
    return i18n.t("common:severity.healthy");
  },
  get unknown() {
    return i18n.t("common:severity.unknown");
  },
  get none() {
    return i18n.t("workloadMap:severityLabels.none");
  },
};

// Any FK behind the edge without an index — also on an edge that is dashed
// because a different join condition between the same tables has no FK.
export const isUnindexedFkEdge = (edge) => edge.foreign_keys.some((fk) => !fk.indexed);
