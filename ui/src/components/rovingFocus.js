// Arrow-key handling shared by SegmentedControl and Tabs (WAI-ARIA radio
// group / tablist pattern): Left/Up and Right/Down move to the previous/
// next item with wrap-around, Home/End jump to the ends. Returns the index
// to move to, or null when the key isn't one of these.
export function nextRovingIndex(key, currentIndex, count) {
  if (count === 0) return null;
  switch (key) {
    case "ArrowLeft":
    case "ArrowUp":
      return (currentIndex - 1 + count) % count;
    case "ArrowRight":
    case "ArrowDown":
      return (currentIndex + 1) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}
