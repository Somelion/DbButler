import i18n from "./i18n";
import { useId } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Circle, CornerDownRight } from "lucide-react";
import { api } from "./api";
import { targetKey } from "./queryClient";
import { Alert, Badge, Card, EmptyState, Skeleton, useConfirm } from "./components";
import tone from "./components/tone.module.css";
import styles from "./Activity.module.css";

const POLL_INTERVAL_MS = 5000;

export default function Activity({ targetId }) {
  const { t } = useTranslation("activity");
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const activityKey = targetKey(targetId, "activity");

  const { data, error, isPending } = useQuery({
    queryKey: activityKey,
    queryFn: ({ signal }) => api.getActivity(targetId, { signal }),
    refetchInterval: POLL_INTERVAL_MS,
  });
  const sessions = data?.sessions ?? [];

  const actionMutation = useMutation({
    mutationFn: ({ pid, action }) =>
      action === "terminate" ? api.terminateSession(targetId, pid) : api.cancelQuery(targetId, pid),
    onSettled: () => queryClient.invalidateQueries({ queryKey: activityKey }),
  });
  const busyPid = actionMutation.isPending ? actionMutation.variables.pid : null;

  const handleAction = async (pid, action) => {
    const terminate = action === "terminate";
    const confirmed = await confirm(
      terminate
        ? {
            title: t("confirmTerminate.title", { pid }),
            message: t("confirmTerminate.message"),
            confirmLabel: t("confirmTerminate.confirm"),
            tone: "danger",
          }
        : {
            title: t("confirmCancel.title", { pid }),
            message: t("confirmCancel.message"),
            confirmLabel: t("confirmCancel.confirm"),
            cancelLabel: t("confirmCancel.keep"),
          }
    );
    if (!confirmed) return;
    actionMutation.mutate({ pid, action });
  };

  return (
    <Card
      actions={
        !isPending && (
          <span className={styles.meta}>{t("sessions", { count: sessions.length })}</span>
        )
      }
    >
      {error && (
        <Alert tone="critical" compact>
          {error.message}
        </Alert>
      )}
      {actionMutation.error && (
        <Alert tone="critical" compact>
          {actionMutation.error.message}
        </Alert>
      )}
      {isPending && <Skeleton lines={4} label={t("loading")} />}
      {data && sessions.length === 0 && <EmptyState title={t("empty")} />}
      <ApplicationBreakdown sessions={sessions} />
      <LockChains sessions={sessions} />
      {sessions.length > 0 && (
        <ul className={styles.sessionList}>
          {sessions.map((session) => (
            <SessionRow key={session.pid} session={session} busy={busyPid === session.pid} onAction={handleAction} />
          ))}
        </ul>
      )}
    </Card>
  );
}

// A quick "which app is actually connected here" summary — pure
// client-side aggregation of sessions the screen already fetched, no
// separate backend query. Only worth showing once there's more than one
// distinct application to break down; a single-app target gets no value
// from a one-item bar.
function ApplicationBreakdown({ sessions }) {
  const { t } = useTranslation("activity");
  const counts = new Map();
  for (const session of sessions) {
    const name = session.application_name || t("unsetApplication");
    counts.set(name, (counts.get(name) || 0) + 1);
  }
  const entries = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  if (entries.length < 2) return null;

  return (
    <ul className={styles.breakdown} aria-label={t("byApplication")}>
      {entries.map(([name, count]) => (
        <li key={name}>
          <Badge tone="neutral">
            {name} · {count}
          </Badge>
        </li>
      ))}
    </ul>
  );
}

function formatDuration(seconds) {
  if (seconds == null) return "—";
  if (seconds < 60) return i18n.t("common:time.durationSeconds", { s: seconds });
  return i18n.t("common:time.durationMinutes", { m: Math.floor(seconds / 60), s: seconds % 60 });
}

// Renders each independent blocking chain root-first, with everyone that
// root (transitively) blocks nested underneath. blocked_by_pids from
// pg_blocking_pids() is already the full transitive ancestor set, not just
// the immediate blocker, so its length is a decent proxy for chain depth in
// the typical linear-chain or single-root-star shapes blocking actually
// takes in practice — good enough for a visual grouping without a second,
// heavier pg_locks self-join to derive exact direct-parent edges.
function LockChains({ sessions }) {
  const { t } = useTranslation("activity");
  const headingId = useId();
  const roots = sessions.filter((s) => s.blocked_by_pids.length === 0 && s.blocking_pids.length > 0);
  const blocked = sessions
    .filter((s) => s.blocked_by_pids.length > 0)
    .sort((a, b) => a.blocked_by_pids.length - b.blocked_by_pids.length || a.pid - b.pid);

  if (roots.length === 0 && blocked.length === 0) return null;

  return (
    <section className={`${styles.lockChains} ${tone.critical}`} aria-labelledby={headingId}>
      <h3 id={headingId} className={styles.lockChainsTitle}>
        {t("lockChains", { count: roots.length })}
      </h3>
      <ul className={styles.lockChainList}>
        {roots.map((root) => (
          <LockChainLine key={root.pid} session={root} depth={0} />
        ))}
        {blocked.map((session) => (
          <LockChainLine key={session.pid} session={session} depth={session.blocked_by_pids.length} />
        ))}
      </ul>
    </section>
  );
}

// Indented by depth (an inline custom property, since it's data-driven);
// the icon is decorative, so the role and depth are spelled out for screen
// readers instead.
function LockChainLine({ session, depth }) {
  const { t } = useTranslation("activity");
  return (
    <li className={styles.lockChainLine} style={{ "--depth": depth }}>
      {depth > 0 ? (
        <CornerDownRight size={12} className={styles.lockChainIcon} aria-hidden="true" />
      ) : (
        <Circle size={8} className={`${styles.lockChainIcon} ${styles.rootIcon}`} aria-hidden="true" />
      )}
      <span className="sr-only">{depth > 0 ? t("blockedDepth", { depth }) : t("blockingRoot")}</span>
      <span className={styles.chainPid}>pid {session.pid}</span>
      <span className={styles.chainUser}>{session.usename}</span>
      {session.waiting_lock && (
        <span className={styles.chainWaiting}>{t("waitingOn", { lock: session.waiting_lock })}</span>
      )}
      {depth > 0 && (
        <span className={styles.chainBlockedBy}>
          {t("chainBlockedBy", {
            count: session.blocked_by_pids.length,
            pids: session.blocked_by_pids.join(", "),
          })}
        </span>
      )}
    </li>
  );
}

function SessionRow({ session, busy, onAction }) {
  const { t } = useTranslation("activity");
  const isBlocked = session.blocked_by_pids.length > 0;
  const isBlocking = session.blocking_pids.length > 0;
  // Tints the card; the "blocked by" / "blocking" text below carries the
  // same status in words.
  const toneClass = isBlocked ? tone.critical : isBlocking ? tone.attention : "";

  return (
    <li className={`${styles.session} ${toneClass}`}>
      <div className={styles.sessionHeader}>
        <div className={styles.sessionInfo}>
          <span className={styles.sessionPid}>pid {session.pid}</span>
          <span className={styles.sessionUser}>{session.usename}</span>
          <span className={styles.statePill}>{session.state ?? t("stateUnknown")}</span>
          {isBlocked && (
            <span className={styles.blockedBy}>
              {t("blockedBy", { count: session.blocked_by_pids.length, pids: session.blocked_by_pids.join(", ") })}
            </span>
          )}
          {isBlocking && (
            <span className={styles.blocking}>{t("blocking", { pids: session.blocking_pids.join(", ") })}</span>
          )}
        </div>
        <div className={styles.sessionActions}>
          <span className={styles.duration}>{formatDuration(session.query_duration_seconds)}</span>
          <button
            type="button"
            className="button-secondary"
            disabled={busy}
            aria-label={t("cancelAria", { pid: session.pid })}
            onClick={() => onAction(session.pid, "cancel")}
          >
            {t("cancel")}
          </button>
          <button
            type="button"
            className="button-danger"
            disabled={busy}
            aria-label={t("terminateAria", { pid: session.pid })}
            onClick={() => onAction(session.pid, "terminate")}
          >
            {t("terminate")}
          </button>
        </div>
      </div>
      {session.query && <span className={styles.query}>{session.query}</span>}
    </li>
  );
}
