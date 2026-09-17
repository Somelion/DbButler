// Deterministic column-name -> color mapping so the same column always gets
// the same pill color everywhere it's rendered (Over-Indexed Tables' and
// Covering Index Candidates' index/column coverage tables) — letting a user
// visually spot which indexes on a table overlap on the same column(s).
const PALETTE = [
  { bg: "oklch(91% 0.05 255)", text: "oklch(35% 0.13 255)" }, // blue
  { bg: "oklch(91% 0.05 50)", text: "oklch(35% 0.13 50)" }, // orange
  { bg: "oklch(91% 0.05 150)", text: "oklch(35% 0.13 150)" }, // green
  { bg: "oklch(91% 0.05 330)", text: "oklch(35% 0.13 330)" }, // magenta
  { bg: "oklch(91% 0.05 200)", text: "oklch(35% 0.13 200)" }, // teal
  { bg: "oklch(91% 0.05 25)", text: "oklch(35% 0.13 25)" }, // red
  { bg: "oklch(91% 0.05 290)", text: "oklch(35% 0.13 290)" }, // purple
  { bg: "oklch(91% 0.05 100)", text: "oklch(35% 0.13 100)" }, // olive
];

function hashString(value) {
  let hash = 0;
  for (let i = 0; i < value.length; i++) {
    hash = (hash * 31 + value.charCodeAt(i)) | 0;
  }
  return Math.abs(hash);
}

export function colorForColumn(columnName) {
  return PALETTE[hashString(columnName) % PALETTE.length];
}
