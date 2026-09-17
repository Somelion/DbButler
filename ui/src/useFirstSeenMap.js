import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

// Shared by every screen that renders FindingCard for a category deep_scan_
// findings covers (all seven Advisor tabs, Table Health, and Dashboard/
// Diagnose Now's live findings) — one fetch of this target's currently-open
// persisted findings, keyed by finding_id, so FindingCard can show "first
// seen N days ago" without each screen re-implementing the same lookup.
// Best-effort: a failed fetch just means no badges render, never an error
// shown to the user, since the findings themselves already loaded fine
// through their own screen-specific endpoint.
//
// Returns [map, refresh] — Table Health's "Run Deep Scan Now" calls refresh()
// right after a run completes, so a table's freshly-first-seen finding gets
// its badge without waiting for this hook's own next targetId-change fetch.
export function useFirstSeenMap(targetId) {
  const [map, setMap] = useState({});

  const refresh = useCallback(() => {
    return api
      .getDeepScanFindings(targetId, "open")
      .then((data) => {
        const next = {};
        for (const entry of data.findings) {
          next[entry.finding_id] = entry.first_seen_at;
        }
        setMap(next);
      })
      .catch(() => {});
  }, [targetId]);

  useEffect(() => {
    setMap({});
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetId]);

  return [map, refresh];
}
