import { useCallback, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { targetKey } from "./queryClient";

// Shared by every screen that renders FindingCard for a category deep_scan_
// findings covers (all seven Advisor tabs, Table Health, and Dashboard/
// Diagnose Now's live findings) — one fetch of this target's currently-open
// persisted findings, keyed by finding_id, so FindingCard can show "first
// seen N days ago" without each screen re-implementing the same lookup.
// Cached under one query key, so the screens share a single request.
// Best-effort: a failed fetch just means no badges render, never an error
// shown to the user, since the findings themselves already loaded fine
// through their own screen-specific endpoint.
//
// Returns [map, refresh] — Table Health's "Run Deep Scan Now" calls refresh()
// right after a run completes, so a table's freshly-first-seen finding gets
// its badge without waiting for the next refetch.
const EMPTY = {};

export function useFirstSeenMap(targetId) {
  const queryClient = useQueryClient();
  const queryKey = targetKey(targetId, "deep-scan-findings", "open");

  const { data } = useQuery({
    queryKey,
    queryFn: ({ signal }) => api.getDeepScanFindings(targetId, "open", { signal }),
    staleTime: 60_000,
    retry: false,
  });

  const map = useMemo(() => {
    if (!data) return EMPTY;
    const next = {};
    for (const entry of data.findings) next[entry.finding_id] = entry.first_seen_at;
    return next;
  }, [data]);

  const refresh = useCallback(
    () => queryClient.invalidateQueries({ queryKey: targetKey(targetId, "deep-scan-findings") }),
    [queryClient, targetId]
  );

  return [map, refresh];
}
