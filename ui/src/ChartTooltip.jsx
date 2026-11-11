import styles from "./ChartTooltip.module.css";

// Shared floating tooltip for TrendChart/MultiTrendChart's hover state.
// Positioned by percentage of the chart's SVG viewBox (xPercent), passed in
// as the --tooltip-x custom property, so it tracks correctly regardless of
// how the responsive SVG is scaled. Near either edge it anchors on that
// edge instead of centring, so it never spills outside the chart.
// The parent must be `position: relative`.
export default function ChartTooltip({ xPercent, children }) {
  const align = xPercent > 70 ? styles.alignEnd : xPercent < 30 ? styles.alignStart : styles.alignCenter;

  return (
    <div className={`${styles.tooltip} ${align}`} style={{ "--tooltip-x": `${xPercent}%` }}>
      {children}
    </div>
  );
}

// Shared row styles for the tooltip's contents (timestamp, value lines).
export { styles as chartTooltipStyles };
