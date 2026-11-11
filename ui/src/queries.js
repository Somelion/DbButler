import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { targetKey } from "./queryClient";

// Query definitions used by more than one component. Screen-local queries
// live in the screen itself, following the same key convention.

export const TARGETS_KEY = ["targets"];

export const targetsQuery = () => ({
  queryKey: TARGETS_KEY,
  queryFn: ({ signal }) => api.listTargets({ signal }),
  // Only changes through this UI (create/rename/retest/remove), and those
  // write the result straight into the cache.
  staleTime: Infinity,
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
