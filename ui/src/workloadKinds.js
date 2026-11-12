// Shared vocabulary for the Workload Map's three edge kinds and its node
// colouring modes (graph, legend, relationships table, side panel).

export const EDGE_KINDS = {
  fk: {
    label: "FK, joined",
    tone: "neutral",
    description: "Joined in the workload, and a declared foreign key covers the join.",
  },
  no_fk: {
    label: "Joined, no FK",
    tone: "attention",
    description:
      "Joined in the workload on columns no foreign key covers — a missing constraint, or a join that may need an index on the join column.",
  },
  fk_unused: {
    label: "FK, never joined",
    tone: "unknown",
    description:
      "Foreign key declared, but no tracked query joins on it — a dead relationship, or an FK index that costs writes without serving reads.",
  },
};

export const EDGE_KIND_KEYS = Object.keys(EDGE_KINDS);

export const COLOR_MODES = [
  { value: "health", label: "Health" },
  { value: "seq", label: "Seq-scan heat" },
  { value: "writes", label: "Write heat" },
];

export const COLOR_MODE_DESCRIPTIONS = {
  health: "Worst open finding on the table (from the nightly deep scan).",
  seq: "Rows read by sequential scans in the window — tables read in full, often.",
  writes: "Rows inserted, updated and deleted in the window.",
};

// The node metric each heat mode scales by.
export const HEAT_METRIC = { seq: "seq_tup_read", writes: "writes" };

export const SEVERITY_LABELS = {
  critical: "Critical findings",
  attention: "Needs attention",
  healthy: "Healthy",
  unknown: "Unknown",
  none: "No open findings",
};

// Any FK behind the edge without an index — also on an edge that is dashed
// because a different join condition between the same tables has no FK.
export const isUnindexedFkEdge = (edge) => edge.foreign_keys.some((fk) => !fk.indexed);
