import { useCallback, useRef, useState } from "react";

// Shared drag-to-zoom + hover state for the app's hand-rolled SVG time-series
// charts (TrendChart, MultiTrendChart). Each chart keeps its own W/H/padding
// layout and xAt/yAt scales — this hook only tracks the current time domain
// (full range, or a zoomed-in [min, max] after a drag) and the cursor's
// position in local SVG coordinates, so the chart can render its own
// crosshair/selection box/tooltip on top of whatever it already draws.
//
// All-client-side: zoom re-slices the points already loaded in the browser
// rather than re-fetching a narrower range from the server.
export function useTimeChartInteraction({ fullMin, fullMax, width, padLeft, padRight }) {
  const svgRef = useRef(null);
  const [domain, setDomain] = useState(null); // [min, max] in epoch ms, or null = full range
  const [dragStartX, setDragStartX] = useState(null); // local SVG x (viewBox units)
  const [hoverX, setHoverX] = useState(null);

  const min = domain ? domain[0] : fullMin;
  const max = domain ? domain[1] : fullMax;
  const plotWidth = width - padLeft - padRight;

  const clampX = useCallback((x) => Math.min(Math.max(x, padLeft), width - padRight), [padLeft, padRight, width]);

  const clientXToLocal = useCallback(
    (clientX) => {
      const rect = svgRef.current.getBoundingClientRect();
      if (rect.width === 0) return padLeft;
      return clampX(((clientX - rect.left) / rect.width) * width);
    },
    [width, clampX]
  );

  const localToTime = useCallback(
    (x) => min + ((x - padLeft) / plotWidth) * (max - min),
    [min, max, padLeft, plotWidth]
  );

  const handleMouseDown = (e) => {
    const x = clientXToLocal(e.clientX);
    setDragStartX(x);
    setHoverX(x);
  };
  const handleMouseMove = (e) => setHoverX(clientXToLocal(e.clientX));
  const handleMouseUp = () => {
    if (dragStartX != null && hoverX != null) {
      const dragPixels = Math.abs(hoverX - dragStartX);
      if (dragPixels > Math.max(4, plotWidth * 0.01)) {
        const t1 = localToTime(dragStartX);
        const t2 = localToTime(hoverX);
        setDomain([Math.min(t1, t2), Math.max(t1, t2)]);
      }
    }
    setDragStartX(null);
  };
  const handleMouseLeave = () => {
    setDragStartX(null);
    setHoverX(null);
  };

  const isDragging = dragStartX != null;

  return {
    svgRef,
    min,
    max,
    isZoomed: domain != null,
    resetZoom: () => setDomain(null),
    isDragging,
    dragBoxX1: isDragging ? Math.min(dragStartX, hoverX) : null,
    dragBoxX2: isDragging ? Math.max(dragStartX, hoverX) : null,
    hoverX: !isDragging ? hoverX : null,
    hoverT: !isDragging && hoverX != null ? localToTime(hoverX) : null,
    handlers: {
      onMouseDown: handleMouseDown,
      onMouseMove: handleMouseMove,
      onMouseUp: handleMouseUp,
      onMouseLeave: handleMouseLeave,
      onDoubleClick: () => setDomain(null),
    },
  };
}

// Nearest point to time `t` (epoch ms) in a points array — used to snap the
// hover crosshair/tooltip to an actual sample instead of an interpolated one.
export function findNearestPoint(points, t) {
  let nearest = null;
  let bestDiff = Infinity;
  for (const p of points) {
    const diff = Math.abs(new Date(p.ts).getTime() - t);
    if (diff < bestDiff) {
      bestDiff = diff;
      nearest = p;
    }
  }
  return nearest;
}
