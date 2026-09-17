// pg_stat_statements normalizes literal values in the queries it tracks into
// $1/$2/... placeholders — the query text Query Intelligence hands to Explain
// and AI Query Analysis is never directly executable as-is (Postgres treats
// "$1" as an extended-query-protocol bind parameter and errors with "there is
// no parameter $1" since nothing ever binds one). These helpers let the user
// fill in a real value for each placeholder before the query is actually run.

export function extractPlaceholders(query) {
  const found = new Set();
  for (const match of query.matchAll(/\$(\d+)\b/g)) {
    found.add(Number(match[1]));
  }
  return [...found].sort((a, b) => a - b);
}

// Splits query text into plain-text and placeholder segments so a caller can
// render $N tokens highlighted (e.g. in the AI Query Analysis template
// preview) instead of just as plain monospace text among everything else.
export function splitOnPlaceholders(text) {
  const parts = [];
  let lastIndex = 0;
  for (const match of text.matchAll(/\$\d+\b/g)) {
    if (match.index > lastIndex) parts.push({ type: "text", value: text.slice(lastIndex, match.index) });
    parts.push({ type: "placeholder", value: match[0] });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) parts.push({ type: "text", value: text.slice(lastIndex) });
  return parts;
}

export function applyPlaceholderValues(query, values) {
  let result = query;
  for (const n of extractPlaceholders(query)) {
    const value = values[n];
    if (value === undefined || value === "") continue;
    result = result.replace(new RegExp(`\\$${n}\\b`, "g"), value);
  }
  return result;
}
