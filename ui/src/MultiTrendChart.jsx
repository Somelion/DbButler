import { formatMetricValue } from "./format";
import { findNearestPoint, useTimeChartInteraction } from "./useTimeChartInteraction";
import ChartTooltip from "./ChartTooltip";

const W = 640;
const H = 220;
const PAD_L = 46;
const PAD_R = 12;
const PAD_TOP = 12;
const PAD_BOTTOM = 28;
const BOTTOM_Y = H - PAD_BOTTOM;

// Fixed categorical order, never cycled — matches the app's severity
// vocabulary in spirit (identity, not magnitude), assigned by selection
// order. Selection is capped at this length so colors never repeat.
export const MULTI_TREND_PALETTE = [
  "oklch(55% 0.16 255)", // blue
  "oklch(60% 0.15 50)", // orange
  "oklch(56% 0.14 150)", // green
  "oklch(55% 0.18 330)", // magenta
  "oklch(55% 0.13 200)", // teal
  "oklch(55% 0.18 25)", // red
];

function formatTick(ts) {
  return new Date(ts).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function MultiTrendChart({ unit, series }) {
  const allPoints = series.flatMap((s) => s.points);

  if (allPoints.length < 2) {
    return (
      <div style={{ height: 220, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Not enough data yet.</span>
      </div>
    );
  }

  const times = allPoints.map((p) => new Date(p.ts).getTime());
  const fullMin = Math.min(...times);
  const fullMax = Math.max(...times);

  return <Chart unit={unit} series={series} fullMin={fullMin} fullMax={fullMax} />;
}

function Chart({ unit, series, fullMin, fullMax }) {
  const { svgRef, min, max, isZoomed, resetZoom, isDragging, dragBoxX1, dragBoxX2, hoverT, handlers } =
    useTimeChartInteraction({ fullMin, fullMax, width: W, padLeft: PAD_L, padRight: PAD_R });

  const visibleSeries = series.map((s) => ({
    ...s,
    points: s.points.filter((p) => {
      const t = new Date(p.ts).getTime();
      return t >= min && t <= max;
    }),
  }));
  const visibleValues = visibleSeries.flatMap((s) => s.points.map((p) => p.value));

  const xAt = (t) => PAD_L + ((t - min) / Math.max(max - min, 1)) * (W - PAD_L - PAD_R);

  if (visibleValues.length === 0) {
    return (
      <div style={{ height: 220, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 8 }}>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>No data in this range.</span>
        {isZoomed && (
          <button className="button-secondary" onClick={resetZoom} style={{ padding: "4px 10px", fontSize: 11 }}>
            Reset zoom
          </button>
        )}
      </div>
    );
  }

  const vMin = Math.min(...visibleValues);
  const vMax = Math.max(...visibleValues);
  const vPad = (vMax - vMin) * 0.1 || Math.abs(vMax) * 0.1 || 1;
  const yMin = Math.min(0, vMin - vPad);
  const yMax = vMax + vPad;
  const yAt = (v) => BOTTOM_Y - ((v - yMin) / Math.max(yMax - yMin, 0.0001)) * (BOTTOM_Y - PAD_TOP);

  const yTicks = [yMax, (yMin + yMax) / 2, yMin];

  // One nearest point per series at the hovered time, for a multi-line tooltip.
  const hoverPoints =
    hoverT != null
      ? visibleSeries
          .map((s, i) => {
            const point = s.points.length ? findNearestPoint(s.points, hoverT) : null;
            return point ? { name: s.name, point, color: MULTI_TREND_PALETTE[i % MULTI_TREND_PALETTE.length] } : null;
          })
          .filter(Boolean)
      : [];
  const hoverAnchorX = hoverPoints.length ? xAt(new Date(hoverPoints[0].point.ts).getTime()) : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
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
          {yTicks.map((v, i) => (
            <g key={i}>
              <line x1={PAD_L} y1={yAt(v)} x2={W - PAD_R} y2={yAt(v)} stroke="var(--border)" strokeWidth="1" />
              <text x={PAD_L - 6} y={yAt(v) + 3} textAnchor="end" fontSize="9.5" fill="var(--text-muted)">
                {formatMetricValue(v, unit)}
              </text>
            </g>
          ))}

          {visibleSeries.map((s, i) => {
            if (s.points.length === 0) return null;
            const color = MULTI_TREND_PALETTE[i % MULTI_TREND_PALETTE.length];
            const coords = s.points.map((p) => `${xAt(new Date(p.ts).getTime()).toFixed(1)},${yAt(p.value).toFixed(1)}`);
            const last = s.points[s.points.length - 1];
            return (
              <g key={s.name}>
                <path
                  d={`M ${coords.join(" L ")}`}
                  fill="none"
                  stroke={color}
                  strokeWidth="2"
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
                <circle cx={xAt(new Date(last.ts).getTime())} cy={yAt(last.value)} r="3.5" fill={color} stroke="white" strokeWidth="1.5" />
              </g>
            );
          })}

          {hoverAnchorX != null && (
            <line x1={hoverAnchorX} y1={PAD_TOP} x2={hoverAnchorX} y2={BOTTOM_Y} stroke="var(--text-muted)" strokeWidth="1" strokeDasharray="3,3" />
          )}
          {hoverPoints.map(({ point, color }, i) => (
            <circle key={i} cx={xAt(new Date(point.ts).getTime())} cy={yAt(point.value)} r="4.5" fill={color} stroke="white" strokeWidth="2" />
          ))}

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

        {hoverPoints.length > 0 && (
          <ChartTooltip xPercent={(hoverAnchorX / W) * 100}>
            <span style={{ fontSize: 10.5, color: "var(--text-muted)" }}>{formatTick(hoverPoints[0].point.ts)}</span>
            {hoverPoints.map(({ name, point, color }) => (
              <span key={name} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11.5 }}>
                <span style={{ width: 8, height: 8, borderRadius: "50%", background: color, flexShrink: 0 }} />
                <span style={{ color: "var(--text-secondary)" }}>{name}</span>
                <span style={{ fontFamily: "IBM Plex Mono, monospace", fontWeight: 600, color: "var(--text)" }}>
                  {formatMetricValue(point.value, unit)}
                </span>
              </span>
            ))}
          </ChartTooltip>
        )}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
        {series.map((s, i) => {
          const color = MULTI_TREND_PALETTE[i % MULTI_TREND_PALETTE.length];
          const last = s.points[s.points.length - 1];
          return (
            <div key={s.name} style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{ width: 10, height: 2, background: color, borderRadius: 1, flexShrink: 0 }} />
              <span style={{ fontSize: 11.5, color: "var(--text-secondary)" }}>{s.name}</span>
              <span style={{ fontFamily: "IBM Plex Mono, monospace", fontSize: 11.5, color: "var(--text-muted)" }}>
                {last ? formatMetricValue(last.value, unit) : "—"}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
