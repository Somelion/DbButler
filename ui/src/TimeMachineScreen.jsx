import { useEffect, useState } from "react";
import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import { Bot, ChevronLeft, ChevronRight, Pause, Play, Rewind, SkipBack, SkipForward } from "lucide-react";
import styles from "./TimeMachineScreen.module.css";
import tableStyles from "./components/Table.module.css";
import { api } from "./api";
import { formatMs } from "./format";
import { targetKey } from "./queryClient";
import { screenPath } from "./routes";
import { Alert, Badge, Card, Dialog, EmptyState, SegmentedControl, SeverityBadge, Skeleton } from "./components";
import LockTreeView from "./LockTreeView";
import TimelineChart, { MARKER_KINDS } from "./TimelineChart";

const RANGE_OPTIONS = [
  { value: 1, label: "1h" },
  { value: 6, label: "6h" },
  { value: 24, label: "24h" },
  { value: 168, label: "7d" },
];
const DEFAULT_HOURS = 24;
const LIVE_REFRESH_MS = 60_000;
const REPLAY_STEP_MS = 1200;
// The backend's incident gap: a next snapshot further away than this
// belongs to another incident, so playback stops there.
const REPLAY_GAP_MS = 60_000;
const EXPLAIN_MARGIN_MS = 5 * 60_000;
const MARKER_LIST_LIMIT = 40;

const formatDateTime = (value) =>
  new Date(value).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });

const formatDuration = (ms) => {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
};

function validDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

// View state in the URL, so a moment can be shared in a post-mortem:
//   ?hours=1|6|24|168   window length (24 by default)
//   ?until=<ISO>        window end (absent = now, refreshing live)
//   ?at=<ISO>           the moment being inspected
function useTimeParams() {
  const [searchParams, setSearchParams] = useSearchParams();
  const hoursParam = Number(searchParams.get("hours"));
  const hours = RANGE_OPTIONS.some((o) => o.value === hoursParam) ? hoursParam : DEFAULT_HOURS;
  const until = validDate(searchParams.get("until"));
  const at = validDate(searchParams.get("at"));
  // Functional update: the replay timer calls this from an earlier render,
  // and must not roll back a window change the user made in between.
  const update = (changes) =>
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const [key, value] of Object.entries(changes)) {
          if (value == null) next.delete(key);
          else next.set(key, value instanceof Date ? value.toISOString() : String(value));
        }
        return next;
      },
      { replace: true }
    );
  return { hours, until, at, update };
}

function ExplainDialog({ result, error, pending, onClose, open }) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="lg"
      title="What happened? (AI)"
      description="Written by your configured AI provider from the facts below — counts, table and lock names, finding titles. No query text or data was sent."
    >
      {pending && <Skeleton lines={5} label="Asking the AI — this can take a minute or two…" />}
      {error && <Alert tone="critical">{error.message}</Alert>}
      {result && (
        <>
          <p className={styles.narrative}>{result.narrative}</p>
          <details>
            <summary className={styles.factsSummary}>Exactly what was sent</summary>
            <pre className={styles.facts}>{result.facts}</pre>
          </details>
        </>
      )}
    </Dialog>
  );
}

function MomentPanel({ targetId, at, moment, replay }) {
  if (!at) {
    return (
      <Card title="Pick a moment">
        <EmptyState icon={Rewind} title="Nothing selected">
          Click the timeline (or focus it and use the arrow keys), or pick a lock incident or event on the left, to see
          who blocked whom, what ran and what had just changed.
        </EmptyState>
      </Card>
    );
  }
  const data = moment.data;
  return (
    <Card title={`At ${formatDateTime(at)}`}>
      <div className={styles.replay} role="group" aria-label="Step through lock snapshots">
        <button
          type="button"
          className="button-secondary button-sm"
          disabled={!data?.previous_snapshot_at}
          onClick={() => replay.goTo(data.previous_snapshot_at)}
        >
          <SkipBack size={13} aria-hidden="true" />
          Previous snapshot
        </button>
        <button
          type="button"
          className="button-secondary button-sm"
          disabled={!replay.playing && !data?.next_snapshot_at}
          onClick={replay.toggle}
        >
          {replay.playing ? <Pause size={13} aria-hidden="true" /> : <Play size={13} aria-hidden="true" />}
          {replay.playing ? "Pause" : "Play"}
        </button>
        <button
          type="button"
          className="button-secondary button-sm"
          disabled={!data?.next_snapshot_at}
          onClick={() => replay.goTo(data.next_snapshot_at)}
        >
          Next snapshot
          <SkipForward size={13} aria-hidden="true" />
        </button>
      </div>

      {moment.error && <Alert tone="critical">{moment.error.message}</Alert>}
      {moment.isPending && <Skeleton lines={6} label="Loading this moment…" />}

      {data && (
        <>
          <section className={styles.section} aria-labelledby="tm-locks">
            <h3 id="tm-locks" className={styles.sectionTitle}>
              Blocking
            </h3>
            {data.lock_tree ? (
              <>
                <p className={styles.muted} role="status">
                  {data.lock_tree.blocked_count} session{data.lock_tree.blocked_count === 1 ? "" : "s"} blocked
                  {data.lock_tree.max_wait_seconds != null && `, longest wait ${data.lock_tree.max_wait_seconds}s`} —
                  snapshot at {formatDateTime(data.lock_tree.collected_at)}. Query literals are masked.
                </p>
                <LockTreeView tree={data.lock_tree} />
              </>
            ) : (
              <p className={styles.muted} role="status">
                No session was waiting on a lock at this moment (snapshots are taken every 15 seconds while one is).
              </p>
            )}
          </section>

          <section className={styles.section} aria-labelledby="tm-queries">
            <h3 id="tm-queries" className={styles.sectionTitle}>
              Top queries in this interval
            </h3>
            {data.top_queries.length === 0 ? (
              <p className={styles.muted}>No Query History interval covers this moment.</p>
            ) : (
              <div className={tableStyles.wrap}>
                <table className={tableStyles.table}>
                  <caption className="sr-only">
                    Tracked queries by DB time in the collection interval ending{" "}
                    {formatDateTime(data.query_interval_end)}
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col" className={tableStyles.th}>Query</th>
                      <th scope="col" className={`${tableStyles.th} ${tableStyles.numeric}`}>Calls</th>
                      <th scope="col" className={`${tableStyles.th} ${tableStyles.numeric}`}>Mean</th>
                      <th scope="col" className={`${tableStyles.th} ${tableStyles.numeric}`}>DB time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.top_queries.map((q) => (
                      <tr key={q.queryid} className={tableStyles.row}>
                        <td className={`${tableStyles.td} ${tableStyles.mono} ${styles.queryCell}`}>
                          <Link to={screenPath("query-history", targetId, null, { q: q.queryid, focus: q.queryid })}>
                            {q.query}
                          </Link>
                        </td>
                        <td className={`${tableStyles.td} ${tableStyles.numeric}`}>{q.calls.toLocaleString()}</td>
                        <td className={`${tableStyles.td} ${tableStyles.numeric}`}>{formatMs(q.mean_exec_ms)}</td>
                        <td className={`${tableStyles.td} ${tableStyles.numeric}`}>{formatMs(q.db_time_ms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className={styles.section} aria-labelledby="tm-nearby">
            <h3 id="tm-nearby" className={styles.sectionTitle}>
              Changes within 30 minutes
            </h3>
            {data.nearby.length === 0 ? (
              <p className={styles.muted}>None recorded.</p>
            ) : (
              <ul className={styles.list}>
                {data.nearby.map((m, index) => (
                  <li key={`${m.kind}-${m.at}-${index}`} className={styles.listItem}>
                    <Badge tone="neutral">{MARKER_KINDS[m.kind]?.label ?? m.kind}</Badge>
                    <span>
                      {formatDateTime(m.at)} — {m.title}
                      {m.detail && <span className={styles.muted}> · {m.detail}</span>}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className={styles.section} aria-labelledby="tm-findings">
            <h3 id="tm-findings" className={styles.sectionTitle}>
              Findings open at this moment
            </h3>
            {data.open_findings.length === 0 ? (
              <p className={styles.muted}>None from the deep scan.</p>
            ) : (
              <ul className={styles.list}>
                {data.open_findings.map((f) => (
                  <li key={f.id} className={styles.listItem}>
                    <SeverityBadge severity={f.severity} />
                    <span>
                      {f.title} <span className={styles.muted}>· since {formatDateTime(f.since)}</span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </Card>
  );
}

// Incident Time Machine: one correlated timeline per database — metrics,
// lock incidents, plan changes, findings and server events — and, for any
// moment, who blocked whom, what ran and what had just changed.
export default function TimeMachineScreen({ targetId }) {
  const { hours, until, at, update } = useTimeParams();
  const [playing, setPlaying] = useState(false);
  const [explainOpen, setExplainOpen] = useState(false);
  const [explainJobId, setExplainJobId] = useState(null);
  const [showAllMarkers, setShowAllMarkers] = useState(false);
  const untilIso = until ? until.toISOString() : null;

  const timeline = useQuery({
    queryKey: targetKey(targetId, "timeline", hours, untilIso ?? "now"),
    queryFn: ({ signal }) => api.getTimeline(targetId, hours, untilIso, { signal }),
    placeholderData: keepPreviousData,
    refetchInterval: until ? false : LIVE_REFRESH_MS,
  });

  const atIso = at ? at.toISOString() : null;
  const moment = useQuery({
    queryKey: targetKey(targetId, "timeline-moment", atIso),
    queryFn: ({ signal }) => api.getTimelineMoment(targetId, atIso, { signal }),
    enabled: Boolean(atIso),
    placeholderData: keepPreviousData,
  });

  // "Explain" is a background job on the server (an AI can take minutes):
  // start it, then poll until it's no longer pending.
  const startExplain = useMutation({
    mutationFn: ({ start, end }) => api.explainTimeline(targetId, start, end),
    onSuccess: (job) => setExplainJobId(job.job_id),
  });
  const explainJob = useQuery({
    queryKey: targetKey(targetId, "timeline-explain", explainJobId),
    queryFn: ({ signal }) => api.getTimelineExplanation(targetId, explainJobId, { signal }),
    enabled: Boolean(explainJobId),
    refetchInterval: (query) => (query.state.data?.status === "pending" ? 2000 : false),
  });
  const job = explainJob.data;
  const explainPending = startExplain.isPending || (explainJobId != null && (!job || job.status === "pending"));
  const explainError =
    startExplain.error ?? explainJob.error ?? (job?.status === "error" ? new Error(job.error) : null);

  // Replay: step through snapshots, one every REPLAY_STEP_MS, until the
  // next one is further than the incident gap away (another incident).
  const nextSnapshot = moment.data?.next_snapshot_at;
  const currentSnapshot = moment.data?.lock_tree?.collected_at ?? null;
  useEffect(() => {
    if (!playing || moment.isFetching) return undefined;
    const nextMs = nextSnapshot ? new Date(nextSnapshot).getTime() : null;
    const currentMs = currentSnapshot ? new Date(currentSnapshot).getTime() : (at?.getTime() ?? null);
    const timer = setTimeout(() => {
      const ended = nextMs == null || currentMs == null || nextMs <= currentMs || nextMs - currentMs > REPLAY_GAP_MS;
      if (ended) setPlaying(false);
      else update({ at: new Date(nextMs) });
    }, REPLAY_STEP_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `update` is recreated every render but functional; the step only depends on these values
  }, [playing, nextSnapshot, currentSnapshot, moment.isFetching]);

  const replay = {
    playing,
    toggle: () => setPlaying((p) => !p),
    goTo: (iso) => {
      setPlaying(false);
      update({ at: new Date(iso) });
    },
  };

  const startReplay = (incident) => {
    update({ at: new Date(incident.start) });
    setPlaying(true);
  };

  const explainWindow = (start, end) => {
    setExplainOpen(true);
    setExplainJobId(null);
    startExplain.mutate({ start: new Date(start).toISOString(), end: new Date(end).toISOString() });
  };

  const pan = (direction) => {
    const end = (until ?? new Date()).getTime() + direction * (hours * 3_600_000) / 2;
    update({ until: end >= Date.now() ? null : new Date(end) });
  };

  const data = timeline.data;
  const markers = data?.markers ?? [];
  const shownMarkers = showAllMarkers ? markers : markers.slice(0, MARKER_LIST_LIMIT);

  return (
    <div className={styles.screen}>
      <Card>
        <div className={styles.toolbar}>
          <SegmentedControl
            label="Window length"
            options={RANGE_OPTIONS}
            value={hours}
            onChange={(value) => update({ hours: value === DEFAULT_HOURS ? null : value })}
          />
          <div className={styles.pan} role="group" aria-label="Move the window">
            <button type="button" className="button-secondary button-sm" onClick={() => pan(-1)}>
              <ChevronLeft size={13} aria-hidden="true" />
              Earlier
            </button>
            <button type="button" className="button-secondary button-sm" onClick={() => pan(1)} disabled={!until}>
              Later
              <ChevronRight size={13} aria-hidden="true" />
            </button>
            <button type="button" className="button-secondary button-sm" onClick={() => update({ until: null })} disabled={!until}>
              Now
            </button>
          </div>
          {data && (
            <span className={styles.muted}>
              {formatDateTime(data.start)} – {until ? formatDateTime(data.end) : "now (refreshing)"}
            </span>
          )}
          {data && hours <= 24 && (
            <button
              type="button"
              className="button-secondary button-sm"
              onClick={() => explainWindow(data.start, data.end)}
            >
              <Bot size={13} aria-hidden="true" />
              Explain this window
            </button>
          )}
        </div>

        {timeline.error && <Alert tone="critical">{timeline.error.message}</Alert>}
        {timeline.isPending && <Skeleton height={300} label="Loading the timeline…" />}
        {data && (
          <>
            <TimelineChart
              start={data.start}
              end={data.end}
              series={data.series}
              incidents={data.incidents}
              markers={markers}
              at={at}
              onPick={(date) => {
                setPlaying(false);
                update({ at: date });
              }}
              label="Timeline. Pick a moment: arrow keys move 1% of the window, Page Up and Page Down 10%, Home and End jump to the ends."
            />
            <div className={styles.legend} aria-hidden="true">
              <span className={styles.legendBand} /> Lock incident
              {Object.entries(MARKER_KINDS).map(([kind, meta]) => (
                <span key={kind} className={styles.legendItem}>
                  <span className={`${styles.legendDot} ${styles[`dot_${kind}`]}`} />
                  {meta.label}
                </span>
              ))}
            </div>
          </>
        )}
      </Card>

      <div className={styles.columns}>
        <div className={styles.side}>
          <Card title={`Lock incidents (${data?.incidents.length ?? 0})`}>
            {data && data.incidents.length === 0 && (
              <p className={styles.muted}>
                No session waited on a lock in this window. Lock trees are recorded every 15 seconds while one does
                (demo: <code>demo/stage-lock-incident.sh</code> stages one).
              </p>
            )}
            <ul className={styles.list}>
              {[...(data?.incidents ?? [])].reverse().map((incident) => {
                const durationMs = new Date(incident.end) - new Date(incident.start);
                return (
                  <li key={incident.start} className={styles.incident}>
                    <span className={styles.incidentTitle}>{formatDateTime(incident.start)}</span>
                    <span className={styles.muted}>
                      ~{formatDuration(durationMs + 15_000)} · up to {incident.max_blocked} blocked
                      {incident.max_wait_seconds != null && ` · longest wait ${incident.max_wait_seconds}s`} ·{" "}
                      {incident.snapshots} snapshot{incident.snapshots === 1 ? "" : "s"}
                    </span>
                    <span className={styles.incidentActions}>
                      <button type="button" className="button-secondary button-sm" onClick={() => startReplay(incident)}>
                        <Play size={13} aria-hidden="true" />
                        Replay
                      </button>
                      <button
                        type="button"
                        className="button-secondary button-sm"
                        onClick={() =>
                          explainWindow(
                            new Date(incident.start).getTime() - EXPLAIN_MARGIN_MS,
                            new Date(incident.end).getTime() + EXPLAIN_MARGIN_MS
                          )
                        }
                      >
                        <Bot size={13} aria-hidden="true" />
                        Explain
                      </button>
                    </span>
                  </li>
                );
              })}
            </ul>
          </Card>

          <Card title={`Changes and events (${markers.length})`}>
            {data && markers.length === 0 && <p className={styles.muted}>Nothing recorded in this window.</p>}
            <ul className={styles.list}>
              {shownMarkers.map((m, index) => (
                <li key={`${m.kind}-${m.at}-${index}`}>
                  <button
                    type="button"
                    className={styles.markerButton}
                    onClick={() => {
                      setPlaying(false);
                      update({ at: new Date(m.at) });
                    }}
                  >
                    <span className={`${styles.legendDot} ${styles[`dot_${m.kind}`]}`} aria-hidden="true" />
                    <span className={styles.markerText}>
                      <span className={styles.markerTime}>{formatDateTime(m.at)}</span> {m.title}
                      {m.detail && <span className={styles.muted}> · {m.detail}</span>}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {markers.length > MARKER_LIST_LIMIT && (
              <button type="button" className="button-link" onClick={() => setShowAllMarkers((v) => !v)}>
                {showAllMarkers ? "Show fewer" : `Show all ${markers.length}`}
              </button>
            )}
          </Card>
        </div>

        <MomentPanel targetId={targetId} at={at} moment={moment} replay={replay} />
      </div>

      <ExplainDialog
        open={explainOpen}
        onClose={() => setExplainOpen(false)}
        result={job?.status === "done" ? job : null}
        error={explainError}
        pending={explainPending && !explainError}
      />
    </div>
  );
}
