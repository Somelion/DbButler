import { CornerDownRight, Lock } from "lucide-react";
import styles from "./LockTreeView.module.css";

const formatSeconds = (s) => {
  if (s == null) return "—";
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
};

function SessionLine({ session, pid }) {
  if (!session) {
    return (
      <div className={styles.session}>
        <span className={styles.pid}>
          {pid === 0 ? "A prepared transaction" : `pid ${pid}`}
        </span>
        <span className={styles.meta}>
          {pid === 0
            ? "two-phase commit, no session — see the Vacuum Horizon tile; ROLLBACK PREPARED or COMMIT PREPARED releases it"
            : "not in this database's activity (another database, or it ended)"}
        </span>
      </div>
    );
  }
  const blocked = session.blocked_by.length > 0;
  return (
    <div className={styles.session}>
      <span className={styles.pid}>
        pid {session.pid}
        {session.application_name && <span className={styles.app}> · {session.application_name}</span>}
        {session.usename && <span className={styles.meta}> · {session.usename}</span>}
      </span>
      <span className={styles.meta}>
        {session.state ?? "unknown state"}
        {blocked && session.waiting_on && ` · waiting for ${session.waiting_on}`}
        {blocked && ` · waiting ${formatSeconds(session.wait_seconds ?? session.state_seconds)}`}
        {session.xact_seconds != null && ` · transaction open ${formatSeconds(session.xact_seconds)}`}
      </span>
      {session.query && <code className={styles.query}>{session.query}</code>}
    </div>
  );
}

// One snapshot's blocking chains as a tree: each root blocker, with the
// sessions waiting on it nested below. Query literals arrive masked.
export default function LockTreeView({ tree }) {
  const byPid = new Map(tree.sessions.map((s) => [s.pid, s]));
  const children = new Map();
  for (const session of tree.sessions) {
    for (const blocker of session.blocked_by) {
      if (!children.has(blocker)) children.set(blocker, []);
      children.get(blocker).push(session.pid);
    }
  }

  // Deadlocks are cycles; PostgreSQL resolves them within deadlock_timeout,
  // but a snapshot can catch one — `seen` keeps the walk finite.
  const renderNode = (pid, depth, seen) => {
    const kids = (children.get(pid) ?? []).filter((child) => !seen.has(child));
    const nextSeen = new Set([...seen, pid]);
    return (
      <li key={`${pid}-${depth}`} className={styles.node}>
        <div className={styles.row}>
          {depth === 0 ? (
            <Lock size={13} className={styles.rootIcon} aria-hidden="true" />
          ) : (
            <CornerDownRight size={13} className={styles.childIcon} aria-hidden="true" />
          )}
          <span className="sr-only">{depth === 0 ? "Blocking: " : `Blocked, depth ${depth}: `}</span>
          <SessionLine session={byPid.get(pid)} pid={pid} />
        </div>
        {kids.length > 0 && <ul className={styles.children}>{kids.map((kid) => renderNode(kid, depth + 1, nextSeen))}</ul>}
      </li>
    );
  };

  // A pure deadlock cycle has no root (everyone is blocked by someone):
  // start from the first session so the cycle still shows.
  const unblocked = tree.sessions.filter((s) => !s.blocked_by.length).map((s) => s.pid);
  const roots =
    tree.root_pids.length > 0 ? tree.root_pids : unblocked.length > 0 ? unblocked : tree.sessions.slice(0, 1).map((s) => s.pid);
  return (
    <>
      {tree.root_pids.length === 0 && unblocked.length === 0 && tree.sessions.length > 0 && (
        <p className={styles.meta}>Every session here is blocked by another — a deadlock cycle caught mid-detection.</p>
      )}
      <ul className={styles.tree}>{roots.map((pid) => renderNode(pid, 0, new Set()))}</ul>
    </>
  );
}
