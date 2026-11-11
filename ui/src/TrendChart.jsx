import styles from "./TrendChart.module.css";
import { formatMetricValue } from "./format";
import { findNearestPoint, useTimeChartInteraction } from "./useTimeChartInteraction";
import ChartTooltip, { chartTooltipStyles } from "./ChartTooltip";

const W = 520;
const H = 150;
const PAD_L = 6;
const PAD_R = 6;
const PAD_TOP = 10;
const PAD_BOTTOM = 24;
const BOTTOM_Y = H - PAD_BOTTOM;

function formatTick(ts) {
  return new Date(ts).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

// `bare` skips the card wrapper (background/border/padding) so a caller that
// already renders its own card — Dashboard's cache-hit-rate widget — can
// embed the chart without nesting a card inside a card. `footer` is an
// optional caption below the chart (Trends' capacity-forecast text) — null
// for every other caller, so this is a no-op change for them.
export default function TrendChart({ label, unit, points, thresholdMin, thresholdMax, bare = false, footer = null }) {
  const latest = points.length ? points[points.length - 1].value : null;
  const latestText = formatMetricValue(latest, unit);

  return (
    <div className={bare ? styles.bare : styles.card}>
      <div className={styles.header}>
        <span className={styles.label}>{label}</span>
        <span className={styles.latest}>{latestText}</span>
      </div>

      {points.length < 2 ? (
        <div className={styles.placeholder}>
          <span className={styles.placeholderText}>Collecting data&hellip;</span>
        </div>
      ) : (
        <Chart
          label={label}
          latestText={latestText}
          points={points}
          unit={unit}
          thresholdMin={thresholdMin}
          thresholdMax={thresholdMax}
        />
      )}

      {footer}
    </div>
  );
}

function Chart({ label, latestText, points, unit, thresholdMin, thresholdMax }) {
  const fullMin = new Date(points[0].ts).getTime();
  const fullMax = new Date(points[points.length - 1].ts).getTime();

  const { svgRef, min, max, isZoomed, resetZoom, isDragging, dragBoxX1, dragBoxX2, hoverT, handlers } =
    useTimeChartInteraction({ fullMin, fullMax, width: W, padLeft: PAD_L, padRight: PAD_R });

  const visible = points.filter((p) => {
    const t = new Date(p.ts).getTime();
    return t >= min && t <= max;
  });

  const xAt = (t) => PAD_L + ((t - min) / Math.max(max - min, 1)) * (W - PAD_L - PAD_R);
  const tAt = (p) => new Date(p.ts).getTime();

  if (visible.length < 2) {
    return (
      <div className={styles.placeholder}>
        <span className={styles.placeholderText}>No data in this range.</span>
        {isZoomed && (
          <button type="button" className="button-secondary button-sm" onClick={resetZoom}>
            Reset zoom
          </button>
        )}
      </div>
    );
  }

  const values = visible.map((p) => p.value);
  const hasBand = thresholdMin != null && thresholdMax != null;
  const dataMin = Math.min(...values, hasBand ? thresholdMin : Infinity);
  const dataMax = Math.max(...values, hasBand ? thresholdMax : -Infinity);
  const pad = (dataMax - dataMin) * 0.1 || Math.abs(dataMax) * 0.1 || 1;
  const yMin = dataMin - pad;
  const yMax = dataMax + pad;
  const yAt = (v) => BOTTOM_Y - ((v - yMin) / Math.max(yMax - yMin, 0.0001)) * (BOTTOM_Y - PAD_TOP);

  const coords = visible.map((p) => `${xAt(tAt(p)).toFixed(1)},${yAt(p.value).toFixed(1)}`);
  const lineD = `M ${coords.join(" L ")}`;
  const areaD = `M ${xAt(tAt(visible[0])).toFixed(1)},${BOTTOM_Y} L ${coords.join(" L ")} L ${xAt(tAt(visible[visible.length - 1])).toFixed(1)},${BOTTOM_Y} Z`;

  const bandY = hasBand ? yAt(thresholdMax) : 0;
  const bandHeight = hasBand ? Math.max(yAt(thresholdMin) - yAt(thresholdMax), 0) : 0;

  const lastPoint = visible[visible.length - 1];
  const hoverPoint = hoverT != null ? findNearestPoint(visible, hoverT) : null;

  // The SVG is one image to assistive tech; this sentence stands in for the
  // line (the hover tooltip is a pointer-only extra on top of it).
  const summary =
    `${label}, ${formatTick(min)} to ${formatTick(max)}. ` +
    `Latest ${latestText}; low ${formatMetricValue(Math.min(...values), unit)}, ` +
    `high ${formatMetricValue(Math.max(...values), unit)}.`;

  return (
    <div className={styles.plot}>
      {isZoomed && (
        <button type="button" className={`button-secondary ${styles.resetZoom}`} onClick={resetZoom}>
          Reset zoom
        </button>
      )}
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className={styles.svg} role="img" aria-label={summary} {...handlers}>
        {hasBand && (
          <rect
            x={PAD_L}
            y={bandY}
            width={W - PAD_L - PAD_R}
            height={bandHeight}
            fill="var(--healthy-solid)"
            opacity="0.08"
          />
        )}
        <line x1={PAD_L} y1={BOTTOM_Y} x2={W - PAD_R} y2={BOTTOM_Y} stroke="var(--chart-grid)" strokeWidth="1" />
        <path d={areaD} fill="var(--chart-1)" opacity="0.08" />
        <path d={lineD} fill="none" stroke="var(--chart-1)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        <circle
          cx={xAt(tAt(lastPoint))}
          cy={yAt(lastPoint.value)}
          r="4"
          fill="var(--chart-1)"
          stroke="var(--card)"
          strokeWidth="2"
        />

        {hoverPoint && (
          <g>
            <line
              x1={xAt(tAt(hoverPoint))}
              y1={PAD_TOP}
              x2={xAt(tAt(hoverPoint))}
              y2={BOTTOM_Y}
              stroke="var(--text-muted)"
              strokeWidth="1"
              strokeDasharray="3,3"
            />
            <circle
              cx={xAt(tAt(hoverPoint))}
              cy={yAt(hoverPoint.value)}
              r="4.5"
              fill="var(--chart-1)"
              stroke="var(--card)"
              strokeWidth="2"
            />
          </g>
        )}

        {isDragging && dragBoxX2 - dragBoxX1 > 1 && (
          <rect
            x={dragBoxX1}
            y={PAD_TOP}
            width={dragBoxX2 - dragBoxX1}
            height={BOTTOM_Y - PAD_TOP}
            fill="var(--accent)"
            opacity="0.12"
          />
        )}

        <text x={PAD_L} y={H - 6} fontSize="10" fill="var(--text-muted)">
          {formatTick(min)}
        </text>
        <text x={W - PAD_R} y={H - 6} textAnchor="end" fontSize="10" fill="var(--text-muted)">
          {formatTick(max)}
        </text>
      </svg>

      {hoverPoint && (
        <ChartTooltip xPercent={(xAt(tAt(hoverPoint)) / W) * 100}>
          <span className={chartTooltipStyles.time}>{formatTick(hoverPoint.ts)}</span>
          <span className={chartTooltipStyles.value}>{formatMetricValue(hoverPoint.value, unit)}</span>
        </ChartTooltip>
      )}
    </div>
  );
}
