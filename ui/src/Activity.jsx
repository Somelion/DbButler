import { useEffect, useState } from "react";
import { api } from "./api";

const POLL_INTERVAL_MS = 5000;

export default function Activity({ targetId }) {
  const [sessions, setSessions] = useState([]);
  const [error, setError] = useState(null);
  const [busyPid, setBusyPid] = useState(null);

  const refresh = () =>
    api
      .getActivity(targetId)
      .then((data) => {
        setSessions(data.sessions);
        setError(null);
      })
      .catch((err) => setError(err.message));

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetId]);

  const handleAction = async (pid, action) => {
    const verb = action === "terminate" ? "terminate session" : "cancel the query for session";
    if (!window.confirm(`Really ${verb} ${pid}?`)) return;
    setBusyPid(pid);
    try {
      if (action === "terminate") await api.terminateSession(targetId, pid);
      else await api.cancelQuery(targetId, pid);
      await refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyPid(null);
    }
  };

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
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)" }}>Activity</span>
        <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
          {sessions.length} session{sessions.length === 1 ? "" : "s"}
        </span>
      </div>
      {error && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{error}</span>}
      {sessions.length === 0 && !error && (
        <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>No other sessions right now.</span>
      )}
      <ApplicationBreakdown sessions={sessions} />
      <LockChains sessions={sessions} />
      {sessions.map((session) => (
        <SessionRow key={session.pid} session={session} busy={busyPid === session.pid} onAction={handleAction} />
      ))}
    </div>
  );
}

// A quick "which app is actually connected here" summary — pure
// client-side aggregation of sessions the screen already fetched, no
// separate backend query. Only worth showing once there's more than one
// distinct application to break down; a single-app target gets no value
// from a one-item bar.
function ApplicationBreakdown({ sessions }) {
  const counts = new Map();
  for (const session of sessions) {
    const name = session.application_name || "(unset)";
    counts.set(name, (counts.get(name) || 0) + 1);
  }
  const entries = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  if (entries.length < 2) return null;

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
      {entries.map(([name, count]) => (
        <span
          key={name}
          style={{
            fontSize: 11,
            fontWeight: 600,
            padding: "3px 9px",
            borderRadius: 100,
            background: "oklch(94% 0.004 250)",
            color: "var(--text-secondary)",
          }}
        >
          {name} · {count}
        </span>
      ))}
    </div>
  );
}

function formatDuration(seconds) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

// Renders each independent blocking chain root-first, with everyone that
// root (transitively) blocks nested underneath. blocked_by_pids from
// pg_blocking_pids() is already the full transitive ancestor set, not just
// the immediate blocker, so its length is a decent proxy for chain depth in
// the typical linear-chain or single-root-star shapes blocking actually
// takes in practice — good enough for a visual grouping without a second,
// heavier pg_locks self-join to derive exact direct-parent edges.
function LockChains({ sessions }) {
  const roots = sessions.filter((s) => s.blocked_by_pids.length === 0 && s.blocking_pids.length > 0);
  const blocked = sessions
    .filter((s) => s.blocked_by_pids.length > 0)
    .sort((a, b) => a.blocked_by_pids.length - b.blocked_by_pids.length || a.pid - b.pid);

  if (roots.length === 0 && blocked.length === 0) return null;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 6,
        padding: "10px 12px",
        borderRadius: 10,
        background: "var(--critical-bg)",
        border: "1px solid var(--critical-border)",
      }}
    >
      <span style={{ fontSize: 11.5, fontWeight: 650, color: "var(--critical-text)" }}>
        Lock chain{roots.length === 1 ? "" : "s"}
      </span>
      {roots.map((root) => (
        <LockChainLine key={root.pid} session={root} depth={0} />
      ))}
      {blocked.map((session) => (
        <LockChainLine key={session.pid} session={session} depth={session.blocked_by_pids.length} />
      ))}
    </div>
  );
}

function LockChainLine({ session, depth }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 6, paddingLeft: depth * 18 }}>
      <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{depth > 0 ? "└─" : "●"}</span>
      <span style={{ fontFamily: "IBM Plex Mono, monospace", fontSize: 11.5, fontWeight: 600, color: "var(--text)" }}>
        pid {session.pid}
      </span>
      <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{session.usename}</span>
      {session.waiting_lock && (
        <span style={{ fontSize: 11, color: "var(--critical-text)" }}>waiting on {session.waiting_lock}</span>
      )}
      {depth > 0 && (
        <span style={{ fontSize: 10.5, color: "var(--text-muted)" }}>
          (blocked by {session.blocked_by_pids.join(", ")})
        </span>
      )}
    </div>
  );
}

function SessionRow({ session, busy, onAction }) {
  const isBlocked = session.blocked_by_pids.length > 0;
  const isBlocking = session.blocking_pids.length > 0;
  const background = isBlocked ? "var(--critical-bg)" : isBlocking ? "var(--attention-bg)" : "var(--row-bg)";
  const border = isBlocked ? "var(--critical-border)" : isBlocking ? "var(--attention-border)" : "var(--border)";

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 6,
        padding: "10px 12px",
        borderRadius: 10,
        background,
        border: `1px solid ${border}`,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontFamily: "IBM Plex Mono, monospace", fontSize: 12, fontWeight: 600, color: "var(--text)" }}>
            pid {session.pid}
          </span>
          <span style={{ fontSize: 11.5, color: "var(--text-secondary)" }}>{session.usename}</span>
          <span
            style={{
              fontSize: 10.5,
              fontWeight: 650,
              textTransform: "uppercase",
              letterSpacing: "0.02em",
              padding: "2px 7px",
              borderRadius: 100,
              background: "oklch(94% 0.004 250)",
              color: "var(--text-secondary)",
            }}
          >
            {session.state ?? "unknown"}
          </span>
          {isBlocked && (
            <span style={{ fontSize: 10.5, fontWeight: 650, color: "var(--critical-text)" }}>
              blocked by {session.blocked_by_pids.join(", ")}
            </span>
          )}
          {isBlocking && (
            <span style={{ fontSize: 10.5, fontWeight: 650, color: "var(--attention-text)" }}>
              blocking {session.blocking_pids.join(", ")}
            </span>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
            {formatDuration(session.query_duration_seconds)}
          </span>
          <button className="button-secondary" disabled={busy} onClick={() => onAction(session.pid, "cancel")}>
            Cancel
          </button>
          <button className="button-danger" disabled={busy} onClick={() => onAction(session.pid, "terminate")}>
            Terminate
          </button>
        </div>
      </div>
      {session.query && (
        <span
          style={{
            fontFamily: "IBM Plex Mono, monospace",
            fontSize: 11.5,
            color: "var(--text-muted)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {session.query}
        </span>
      )}
    </div>
  );
}
