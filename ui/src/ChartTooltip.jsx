// Shared floating tooltip for TrendChart/MultiTrendChart's hover state.
// Positioned by percentage of the chart's SVG viewBox (xPercent/yPercent),
// so it tracks correctly regardless of how the responsive SVG is scaled.
// The parent must be `position: relative`.
export default function ChartTooltip({ xPercent, children }) {
  const alignEnd = xPercent > 70;
  const alignStart = xPercent < 30;

  return (
    <div
      style={{
        position: "absolute",
        top: 4,
        left: `${xPercent}%`,
        transform: alignEnd ? "translateX(-100%)" : alignStart ? "translateX(0)" : "translateX(-50%)",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        padding: "6px 9px",
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: 8,
        boxShadow: "0 4px 12px oklch(0% 0 0 / 0.12)",
        pointerEvents: "none",
        whiteSpace: "nowrap",
        zIndex: 1,
      }}
    >
      {children}
    </div>
  );
}
