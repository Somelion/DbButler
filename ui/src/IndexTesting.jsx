import { useEffect, useState } from "react";
import { api } from "./api";
import { formatBytes, formatRelativeTime } from "./format";

const VERDICT = {
  being_used: { label: "Being used", bg: "var(--healthy-bg)", text: "var(--healthy-text)" },
  not_used_yet: { label: "Not used yet", bg: "var(--attention-bg)", text: "var(--attention-text)" },
  too_early: { label: "Too early to tell", bg: "oklch(94% 0.004 250)", text: "var(--text-secondary)" },
};

export default function IndexTesting({ targetId }) {
  const [indexes, setIndexes] = useState(null);
  const [error, setError] = useState(null);

  const load = () => {
    api
      .getIndexTesting(targetId)
      .then((data) => setIndexes(data.indexes))
      .catch((err) => setError(err.message));
  };

  useEffect(() => {
    setError(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetId]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 10,
        padding: "18px 20px",
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: 14,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span style={{ fontFamily: "Manrope, sans-serif", fontWeight: 650, fontSize: 14, color: "var(--text)" }}>
          Index Testing
        </span>
        <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
          Indexes you've added from Index Advisor's Apply button, and whether they're actually earning
          their keep. Once you're happy with one, add it to your own migration tooling (Liquibase, etc.)
          — dropping it here only removes it from this database, not from anywhere else.
        </span>
      </div>

      {error && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{error}</span>}

      {indexes && indexes.length === 0 && (
        <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
          Nothing here yet — use Apply on an Index Advisor finding to create an index and start tracking it.
        </span>
      )}

      {indexes && indexes.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {indexes.map((idx) => (
            <TrackedIndexCard key={idx.id} idx={idx} targetId={targetId} onChanged={load} setError={setError} />
          ))}
        </div>
      )}
    </div>
  );
}

function TrackedIndexCard({ idx, targetId, onChanged, setError }) {
  const [confirming, setConfirming] = useState(false);
  const [dropping, setDropping] = useState(false);
  const verdict = VERDICT[idx.verdict] ?? VERDICT.too_early;

  const handleDrop = () => {
    setDropping(true);
    api
      .dropTrackedIndex(targetId, idx.id)
      .then(onChanged)
      .catch((err) => setError(err.message))
      .finally(() => setDropping(false));
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 10,
        padding: "14px 16px",
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: 12,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 9 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ fontFamily: "IBM Plex Mono, monospace", fontWeight: 650, fontSize: 13.5, color: "var(--text)" }}>
            {idx.index_name}
          </span>
          <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
            on {idx.schema_name}.{idx.table_name} · added {formatRelativeTime(idx.created_at)}
          </span>
        </div>
        <span
          style={{
            padding: "3px 9px",
            borderRadius: 100,
            background: verdict.bg,
            color: verdict.text,
            fontSize: 11,
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.03em",
          }}
        >
          {verdict.label}
        </span>
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 18 }}>
        <Metric label="Scans since added" value={idx.scans_since_added.toLocaleString()} />
        <Metric label="Index size" value={formatBytes(idx.index_bytes)} />
        <Metric
          label="Table's seq scans since added"
          value={idx.seq_scans_since_added.toLocaleString()}
          hint="Raw count, not a rate — for a real before/after trend, watch this table's Seq-Scan-Heavy finding over time too."
        />
      </div>

      {!confirming && (
        <button
          className="button-danger"
          onClick={() => setConfirming(true)}
          style={{ alignSelf: "flex-start", padding: "4px 10px", fontSize: 11 }}
        >
          Drop this index
        </button>
      )}

      {confirming && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 8,
            padding: "10px 12px",
            background: "var(--critical-bg)",
            borderRadius: 8,
          }}
        >
          <span style={{ fontSize: 12, fontWeight: 650, color: "var(--critical-text)" }}>
            Drop {idx.index_name} from {idx.schema_name}.{idx.table_name}? This runs against the target
            immediately and can't be undone from here.
          </span>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="button-danger" onClick={handleDrop} disabled={dropping}>
              {dropping ? "Dropping…" : "Confirm & drop"}
            </button>
            <button className="button-secondary" onClick={() => setConfirming(false)} disabled={dropping}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, hint }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }} title={hint}>
      <span style={{ fontSize: 10.5, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.03em" }}>
        {label}
      </span>
      <span style={{ fontFamily: "IBM Plex Mono, monospace", fontSize: 13, color: "var(--text)" }}>{value}</span>
    </div>
  );
}
