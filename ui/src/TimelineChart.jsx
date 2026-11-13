import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./TimelineChart.module.css";

const WIDTH = 1000;
const PAD_LEFT = 8;
const PAD_RIGHT = 8;
const LANE_HEIGHT = 54;
const LANE_GAP = 10;
const MARKER_LANE = 30;
const AXIS = 22;
const PLOT_WIDTH = WIDTH - PAD_LEFT - PAD_RIGHT;

// labelKey is a key in the timeMachine namespace, translated at render.
export const MARKER_KINDS = {
  plan_change: { labelKey: "markers.plan_change", className: "markerPlan", shape: "diamond" },
  finding_opened: { labelKey: "markers.finding_opened", className: "markerOpened", shape: "triangle" },
  finding_resolved: { labelKey: "markers.finding_resolved", className: "markerResolved", shape: "circle" },
  restart: { labelKey: "markers.restart", className: "markerRestart", shape: "square" },
  stats_reset: { labelKey: "markers.stats_reset", className: "markerReset", shape: "square" },
  config_change: { labelKey: "markers.config_change", className: "markerConfig", shape: "square" },
};

const SERIES_CLASSES = ["line1", "line2", "line3", "line4"];

function markerShape(shape, x, y) {
  const r = 5;
  if (shape === "diamond") return <polygon points={`${x},${y - r} ${x + r},${y} ${x},${y + r} ${x - r},${y}`} />;
  if (shape === "triangle") return <polygon points={`${x},${y - r} ${x + r},${y + r} ${x - r},${y + r}`} />;
  if (shape === "circle") return <circle cx={x} cy={y} r={r - 1} />;
  return <rect x={x - r + 1} y={y - r + 1} width={(r - 1) * 2} height={(r - 1) * 2} />;
}

// The correlated timeline: one lane per metric (each scaled to its own
// range), a lane of event markers, and lock incidents as shaded bands
// across everything. The whole chart is a slider for "the moment":
// click to pick it, or focus it and use the arrow keys (1% of the
// window), Page Up/Down (10%), Home/End. Every marker and incident is also
// listed as text beside the chart, so nothing is only in the picture.
export default function TimelineChart({ start, end, series, incidents, markers, at, onPick, label }) {
  const { t, i18n } = useTranslation("timeMachine");
  const timeFormat = new Intl.DateTimeFormat(i18n.language, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
  const formatTime = (ms) => timeFormat.format(new Date(ms));
  const svgRef = useRef(null);
  const [hoverMs, setHoverMs] = useState(null);
  const startMs = new Date(start).getTime();
  const endMs = new Date(end).getTime();
  const span = Math.max(1, endMs - startMs);
  const xAt = (ms) => PAD_LEFT + ((ms - startMs) / span) * PLOT_WIDTH;
  const height = series.length * (LANE_HEIGHT + LANE_GAP) + MARKER_LANE + AXIS;
  const atMs = at ? new Date(at).getTime() : null;

  const timeFromEvent = (event) => {
    const rect = svgRef.current.getBoundingClientRect();
    const local = ((event.clientX - rect.left) / rect.width) * WIDTH;
    const clamped = Math.min(Math.max(local, PAD_LEFT), WIDTH - PAD_RIGHT);
    return startMs + ((clamped - PAD_LEFT) / PLOT_WIDTH) * span;
  };

  const handleKeyDown = (event) => {
    const base = atMs ?? endMs;
    const steps = { ArrowLeft: -0.01, ArrowRight: 0.01, ArrowDown: -0.01, ArrowUp: 0.01, PageDown: -0.1, PageUp: 0.1 };
    let next = null;
    if (event.key in steps) next = base + steps[event.key] * span;
    else if (event.key === "Home") next = startMs;
    else if (event.key === "End") next = endMs;
    if (next == null) return;
    event.preventDefault();
    onPick(new Date(Math.min(Math.max(next, startMs), endMs)));
  };

  const ticks = Array.from({ length: 6 }, (_, i) => startMs + (span * i) / 5);
  const markerY = series.length * (LANE_HEIGHT + LANE_GAP) + MARKER_LANE / 2;

  return (
    <div className={styles.wrap}>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${WIDTH} ${height}`}
        className={styles.svg}
        role="slider"
        tabIndex={0}
        aria-label={label}
        aria-valuemin={startMs}
        aria-valuemax={endMs}
        aria-valuenow={atMs ?? endMs}
        aria-valuetext={atMs ? t("chart.moment", { time: formatTime(atMs) }) : t("chart.noMoment")}
        onKeyDown={handleKeyDown}
        onPointerDown={(e) => onPick(new Date(timeFromEvent(e)))}
        onPointerMove={(e) => setHoverMs(timeFromEvent(e))}
        onPointerLeave={() => setHoverMs(null)}
      >
        {incidents.map((incident) => {
          const x1 = xAt(new Date(incident.start).getTime());
          // A one-snapshot incident still gets a visible band.
          const x2 = Math.max(xAt(new Date(incident.end).getTime()), x1 + 3);
          return (
            <rect
              key={incident.start}
              x={x1}
              y={0}
              width={x2 - x1}
              height={height - AXIS}
              className={styles.incident}
            >
              <title>
                {t("chart.incidentTitle", {
                  n: incident.max_blocked,
                  time: formatTime(new Date(incident.start).getTime()),
                })}
              </title>
            </rect>
          );
        })}

        {series.map((line, index) => {
          const top = index * (LANE_HEIGHT + LANE_GAP);
          const values = line.points.map((p) => p.value);
          const min = values.length ? Math.min(...values) : 0;
          const max = values.length ? Math.max(...values) : 1;
          const range = max - min || 1;
          const yAt = (v) => top + LANE_HEIGHT - 4 - ((v - min) / range) * (LANE_HEIGHT - 12);
          const path = line.points
            .map((p, i) => `${i === 0 ? "M" : "L"}${xAt(new Date(p.ts).getTime()).toFixed(1)},${yAt(p.value).toFixed(1)}`)
            .join(" ");
          return (
            <g key={line.metric}>
              <rect x={PAD_LEFT} y={top} width={PLOT_WIDTH} height={LANE_HEIGHT} className={styles.lane} />
              {path && <path d={path} className={`${styles.line} ${styles[SERIES_CLASSES[index % 4]]}`} />}
              <text x={PAD_LEFT + 6} y={top + 13} className={styles.laneLabel}>
                {line.label}
                {values.length > 0 &&
                  ` · ${Math.round(min * 10) / 10}–${Math.round(max * 10) / 10}${line.unit === "%" ? "%" : ""}`}
                {values.length === 0 && t("chart.noData")}
              </text>
            </g>
          );
        })}

        <line x1={PAD_LEFT} x2={WIDTH - PAD_RIGHT} y1={markerY} y2={markerY} className={styles.markerAxis} />
        {markers.map((marker, index) => {
          const kind = MARKER_KINDS[marker.kind] ?? MARKER_KINDS.config_change;
          return (
            <g key={`${marker.kind}-${marker.at}-${index}`} className={styles[kind.className]}>
              {markerShape(kind.shape, xAt(new Date(marker.at).getTime()), markerY)}
              <title>
                {formatTime(new Date(marker.at).getTime())} — {marker.title}
              </title>
            </g>
          );
        })}

        {ticks.map((ms) => (
          <text key={ms} x={xAt(ms)} y={height - 6} className={styles.tick} textAnchor="middle">
            {formatTime(ms)}
          </text>
        ))}

        {hoverMs != null && (
          <line x1={xAt(hoverMs)} x2={xAt(hoverMs)} y1={0} y2={height - AXIS} className={styles.hover} />
        )}
        {atMs != null && atMs >= startMs && atMs <= endMs && (
          <line x1={xAt(atMs)} x2={xAt(atMs)} y1={0} y2={height - AXIS} className={styles.cursor} />
        )}
      </svg>
      {hoverMs != null && <span className={styles.hoverLabel}>{formatTime(hoverMs)}</span>}
    </div>
  );
}
