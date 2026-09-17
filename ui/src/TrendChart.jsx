import { formatMetricValue } from "./format";
import { findNearestPoint, useTimeChartInteraction } from "./useTimeChartInteraction";
import ChartTooltip from "./ChartTooltip";

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

  const content = (
    <>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-secondary)" }}>{label}</span>
        <span style={{ fontFamily: "IBM Plex Mono, monospace", fontSize: 16, fontWeight: 600, color: "var(--text)" }}>
          {formatMetricValue(latest, unit)}
        </span>
      </div>

      {points.length < 2 ? (
        <div style={{ height: 120, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Collecting data&hellip;</span>
        </div>
      ) : (
        <Chart points={points} unit={unit} thresholdMin={thresholdMin} thresholdMax={thresholdMax} />
      )}

      {footer}
    </>
  );

  if (bare) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {content}
      </div>
    );
  }

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
      {content}
    </div>
  );
}

function Chart({ points, unit, thresholdMin, thresholdMax }) {
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
      <div style={{ height: 120, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 8 }}>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>No data in this range.</span>
        {isZoomed && (
          <button className="button-secondary" onClick={resetZoom} style={{ padding: "4px 10px", fontSize: 11 }}>
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

  return (
    <div style={{ position: "relative" }}>
      {isZoomed && (
        <button
          className="button-secondary"
          onClick={resetZoom}
          style={{ position: "absolute", top: 0, right: 0, padding: "3px 9px", fontSize: 10.5, zIndex: 1 }}
        >
          Reset zoom
        </button>
      )}
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: "100%", height: "auto", display: "block", overflow: "visible", cursor: "crosshair" }}
        {...handlers}
      >
        {hasBand && (
          <rect x={PAD_L} y={bandY} width={W - PAD_L - PAD_R} height={bandHeight} fill="var(--healthy-text)" opacity="0.08" />
        )}
        <line x1={PAD_L} y1={BOTTOM_Y} x2={W - PAD_R} y2={BOTTOM_Y} stroke="var(--border)" strokeWidth="1" />
        <path d={areaD} fill="var(--accent)" opacity="0.08" />
        <path d={lineD} fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={xAt(tAt(lastPoint))} cy={yAt(lastPoint.value)} r="4" fill="var(--accent)" stroke="white" strokeWidth="2" />

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
            <circle cx={xAt(tAt(hoverPoint))} cy={yAt(hoverPoint.value)} r="4.5" fill="var(--accent)" stroke="white" strokeWidth="2" />
          </g>
        )}

        {isDragging && dragBoxX2 - dragBoxX1 > 1 && (
          <rect x={dragBoxX1} y={PAD_TOP} width={dragBoxX2 - dragBoxX1} height={BOTTOM_Y - PAD_TOP} fill="var(--accent)" opacity="0.12" />
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
          <span style={{ fontSize: 10.5, color: "var(--text-muted)" }}>{formatTick(hoverPoint.ts)}</span>
          <span style={{ fontFamily: "IBM Plex Mono, monospace", fontSize: 12, fontWeight: 600, color: "var(--text)" }}>
            {formatMetricValue(hoverPoint.value, unit)}
          </span>
        </ChartTooltip>
      )}
    </div>
  );
}
