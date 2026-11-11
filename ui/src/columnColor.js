// Deterministic column-name -> color mapping so the same column always gets
// the same pill color everywhere it's rendered (Over-Indexed Tables' and
// Covering Index Candidates' index/column coverage tables) — letting a user
// visually spot which indexes on a table overlap on the same column(s).
//
// Returns CSS colour values for a bg/text pair. The actual hues (blue,
// orange, green, magenta, teal, red, purple, olive) live in tokens.css as
// --column-1-* … --column-8-*, with a light and a dark variant each.
const PALETTE_SIZE = 8;

function hashString(value) {
  let hash = 0;
  for (let i = 0; i < value.length; i++) {
    hash = (hash * 31 + value.charCodeAt(i)) | 0;
  }
  return Math.abs(hash);
}

export function colorForColumn(columnName) {
  const slot = (hashString(columnName) % PALETTE_SIZE) + 1;
  return { bg: `var(--column-${slot}-bg)`, text: `var(--column-${slot}-text)` };
}
