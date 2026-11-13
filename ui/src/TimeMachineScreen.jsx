import { useEffect, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
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

// labelKey is a key in the timeMachine namespace, translated at render.
const RANGE_OPTIONS = [
  { value: 1, labelKey: "ranges.1h" },
  { value: 6, labelKey: "ranges.6h" },
  { value: 24, labelKey: "ranges.24h" },
  { value: 168, labelKey: "ranges.7d" },
];
const DEFAULT_HOURS = 24;
const LIVE_REFRESH_MS = 60_000;
const REPLAY_STEP_MS = 1200;
// The backend's incident gap: a next snapshot further away than this
// belongs to another incident, so playback stops there.
const REPLAY_GAP_MS = 60_000;
const EXPLAIN_MARGIN_MS = 5 * 60_000;
const MARKER_LIST_LIMIT = 40;

// Date and time in the UI language; built per render so a language
// switch takes effect.
function useFormatDateTime() {
  const { i18n } = useTranslation();
  const format = new Intl.DateTimeFormat(i18n.language, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  return (value) => format.format(new Date(value));
}

const formatDuration = (ms, t) => {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return t("duration.seconds", { s });
  if (s < 3600) return t("duration.minutes", { m: Math.floor(s / 60), s: s % 60 });
  return t("duration.hours", { h: Math.floor(s / 3600), m: Math.floor((s % 3600) / 60) });
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
  const { t } = useTranslation("timeMachine");
  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="lg"
      title={t("explain.title")}
      description={t("explain.description")}
    >
      {pending && <Skeleton lines={5} label={t("explain.asking")} />}
      {error && <Alert tone="critical">{error.message}</Alert>}
      {result && (
        <>
          <p className={styles.narrative}>{result.narrative}</p>
          <details>
            <summary className={styles.factsSummary}>{t("explain.whatWasSent")}</summary>
            <pre className={styles.facts}>{result.facts}</pre>
          </details>
        </>
      )}
    </Dialog>
  );
}

function MomentPanel({ targetId, at, moment, replay }) {
  const { t } = useTranslation("timeMachine");
  const formatDateTime = useFormatDateTime();
  if (!at) {
    return (
      <Card title={t("moment.pickTitle")}>
        <EmptyState icon={Rewind} title={t("moment.nothingSelected")}>
          {t("moment.pickHint")}
        </EmptyState>
      </Card>
    );
  }
  const data = moment.data;
  return (
    <Card title={t("moment.title", { time: formatDateTime(at) })}>
      <div className={styles.replay} role="group" aria-label={t("moment.stepThrough")}>
        <button
          type="button"
          className="button-secondary button-sm"
          disabled={!data?.previous_snapshot_at}
          onClick={() => replay.goTo(data.previous_snapshot_at)}
        >
          <SkipBack size={13} aria-hidden="true" />
          {t("moment.previousSnapshot")}
        </button>
        <button
          type="button"
          className="button-secondary button-sm"
          disabled={!replay.playing && !data?.next_snapshot_at}
          onClick={replay.toggle}
        >
          {replay.playing ? <Pause size={13} aria-hidden="true" /> : <Play size={13} aria-hidden="true" />}
          {replay.playing ? t("moment.pause") : t("moment.play")}
        </button>
        <button
          type="button"
          className="button-secondary button-sm"
          disabled={!data?.next_snapshot_at}
          onClick={() => replay.goTo(data.next_snapshot_at)}
        >
          {t("moment.nextSnapshot")}
          <SkipForward size={13} aria-hidden="true" />
        </button>
      </div>

      {moment.error && <Alert tone="critical">{moment.error.message}</Alert>}
      {moment.isPending && <Skeleton lines={6} label={t("moment.loading")} />}

      {data && (
        <>
          <section className={styles.section} aria-labelledby="tm-locks">
            <h3 id="tm-locks" className={styles.sectionTitle}>
              {t("moment.blocking")}
            </h3>
            {data.lock_tree ? (
              <>
                <p className={styles.muted} role="status">
                  {t("moment.sessionsBlocked", { count: data.lock_tree.blocked_count })}
                  {data.lock_tree.max_wait_seconds != null &&
                    `, ${t("longestWait", { seconds: data.lock_tree.max_wait_seconds })}`}
                  {t("moment.snapshotAt", { time: formatDateTime(data.lock_tree.collected_at) })}
                </p>
                <LockTreeView tree={data.lock_tree} />
              </>
            ) : (
              <p className={styles.muted} role="status">
                {t("moment.noLockWait")}
              </p>
            )}
          </section>

          <section className={styles.section} aria-labelledby="tm-queries">
            <h3 id="tm-queries" className={styles.sectionTitle}>
              {t("moment.topQueries")}
            </h3>
            {data.top_queries.length === 0 ? (
              <p className={styles.muted}>{t("moment.noQueryInterval")}</p>
            ) : (
              <div className={tableStyles.wrap}>
                <table className={tableStyles.table}>
                  <caption className="sr-only">
                    {t("moment.queriesCaption", { time: formatDateTime(data.query_interval_end) })}
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col" className={tableStyles.th}>{t("moment.colQuery")}</th>
                      <th scope="col" className={`${tableStyles.th} ${tableStyles.numeric}`}>{t("moment.colCalls")}</th>
                      <th scope="col" className={`${tableStyles.th} ${tableStyles.numeric}`}>{t("moment.colMean")}</th>
                      <th scope="col" className={`${tableStyles.th} ${tableStyles.numeric}`}>{t("moment.colDbTime")}</th>
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
              {t("moment.nearby")}
            </h3>
            {data.nearby.length === 0 ? (
              <p className={styles.muted}>{t("moment.noneRecorded")}</p>
            ) : (
              <ul className={styles.list}>
                {data.nearby.map((m, index) => (
                  <li key={`${m.kind}-${m.at}-${index}`} className={styles.listItem}>
                    <Badge tone="neutral">{MARKER_KINDS[m.kind] ? t(MARKER_KINDS[m.kind].labelKey) : m.kind}</Badge>
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
              {t("moment.openFindings")}
            </h3>
            {data.open_findings.length === 0 ? (
              <p className={styles.muted}>{t("moment.noOpenFindings")}</p>
            ) : (
              <ul className={styles.list}>
                {data.open_findings.map((f) => (
                  <li key={f.id} className={styles.listItem}>
                    <SeverityBadge severity={f.severity} />
                    <span>
                      {f.title}{" "}
                      <span className={styles.muted}>· {t("moment.since", { time: formatDateTime(f.since) })}</span>
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
  const { t } = useTranslation("timeMachine");
  const formatDateTime = useFormatDateTime();
  const rangeOptions = RANGE_OPTIONS.map((o) => ({ value: o.value, label: t(o.labelKey) }));
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
            label={t("toolbar.windowLength")}
            options={rangeOptions}
            value={hours}
            onChange={(value) => update({ hours: value === DEFAULT_HOURS ? null : value })}
          />
          <div className={styles.pan} role="group" aria-label={t("toolbar.moveWindow")}>
            <button type="button" className="button-secondary button-sm" onClick={() => pan(-1)}>
              <ChevronLeft size={13} aria-hidden="true" />
              {t("toolbar.earlier")}
            </button>
            <button type="button" className="button-secondary button-sm" onClick={() => pan(1)} disabled={!until}>
              {t("toolbar.later")}
              <ChevronRight size={13} aria-hidden="true" />
            </button>
            <button type="button" className="button-secondary button-sm" onClick={() => update({ until: null })} disabled={!until}>
              {t("toolbar.now")}
            </button>
          </div>
          {data && (
            <span className={styles.muted}>
              {formatDateTime(data.start)} – {until ? formatDateTime(data.end) : t("toolbar.nowRefreshing")}
            </span>
          )}
          {data && hours <= 24 && (
            <button
              type="button"
              className="button-secondary button-sm"
              onClick={() => explainWindow(data.start, data.end)}
            >
              <Bot size={13} aria-hidden="true" />
              {t("toolbar.explainWindow")}
            </button>
          )}
        </div>

        {timeline.error && <Alert tone="critical">{timeline.error.message}</Alert>}
        {timeline.isPending && <Skeleton height={300} label={t("toolbar.loadingTimeline")} />}
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
              label={t("toolbar.chartLabel")}
            />
            <div className={styles.legend} aria-hidden="true">
              <span className={styles.legendBand} /> {t("toolbar.lockIncident")}
              {Object.entries(MARKER_KINDS).map(([kind, meta]) => (
                <span key={kind} className={styles.legendItem}>
                  <span className={`${styles.legendDot} ${styles[`dot_${kind}`]}`} />
                  {t(meta.labelKey)}
                </span>
              ))}
            </div>
          </>
        )}
      </Card>

      <div className={styles.columns}>
        <div className={styles.side}>
          <Card title={t("incidents.title", { n: data?.incidents.length ?? 0 })}>
            {data && data.incidents.length === 0 && (
              <p className={styles.muted}>
                <Trans t={t} i18nKey="incidents.none" components={{ code: <code /> }} />
              </p>
            )}
            <ul className={styles.list}>
              {[...(data?.incidents ?? [])].reverse().map((incident) => {
                const durationMs = new Date(incident.end) - new Date(incident.start);
                return (
                  <li key={incident.start} className={styles.incident}>
                    <span className={styles.incidentTitle}>{formatDateTime(incident.start)}</span>
                    <span className={styles.muted}>
                      ~{formatDuration(durationMs + 15_000, t)} · {t("incidents.upToBlocked", { n: incident.max_blocked })}
                      {incident.max_wait_seconds != null &&
                        ` · ${t("longestWait", { seconds: incident.max_wait_seconds })}`}{" "}
                      · {t("incidents.snapshots", { count: incident.snapshots })}
                    </span>
                    <span className={styles.incidentActions}>
                      <button type="button" className="button-secondary button-sm" onClick={() => startReplay(incident)}>
                        <Play size={13} aria-hidden="true" />
                        {t("incidents.replay")}
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
                        {t("incidents.explain")}
                      </button>
                    </span>
                  </li>
                );
              })}
            </ul>
          </Card>

          <Card title={t("events.title", { n: markers.length })}>
            {data && markers.length === 0 && <p className={styles.muted}>{t("events.none")}</p>}
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
                {showAllMarkers ? t("events.showFewer") : t("events.showAll", { n: markers.length })}
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
