import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { targetKey } from "./queryClient";

// Query definitions used by more than one component. Screen-local queries
// live in the screen itself, following the same key convention.

export const TARGETS_KEY = ["targets"];

export const targetsQuery = () => ({
  queryKey: TARGETS_KEY,
  queryFn: ({ signal }) => api.listTargets({ signal }),
  // Changes through this UI (create/rename/retest/remove) write the result
  // straight into the cache. The background jobs also update each
  // connection's status (reachable or not, last_test_ok), so it's reread
  // every minute for the database switcher's status dot.
  staleTime: Infinity,
  refetchInterval: 60_000,
});

export const useTargets = () => useQuery(targetsQuery());

export const queryAnalysesQuery = (targetId) => ({
  queryKey: targetKey(targetId, "query-analyses"),
  queryFn: ({ signal }) => api.getQueryAnalyses(targetId, { signal }),
});

export const extensionStatusQuery = (targetId) => ({
  queryKey: targetKey(targetId, "extension-status"),
  queryFn: ({ signal }) => api.getExtensionStatus(targetId, { signal }),
  // Whether pg_stat_statements is installed rarely changes mid-session.
  staleTime: 5 * 60_000,
});

export const dashboardSettingsQuery = () => ({
  queryKey: ["dashboard-settings"],
  queryFn: ({ signal }) => api.getDashboardSettings({ signal }),
});
