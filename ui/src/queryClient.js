import { QueryClient } from "@tanstack/react-query";

// One cache for the whole app. Conventions:
//   - Target-scoped data is keyed targetKey(targetId, "<resource>", ...params),
//     so switching database can never show the previous target's data and
//     invalidating targetKey(id) refreshes everything for that target.
//   - Polling uses `refetchInterval`; TanStack Query pauses it while the tab
//     is hidden (refetchIntervalInBackground stays false).
//   - queryFn always forwards the AbortSignal: ({ signal }) => api.x(id, { signal }).
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      // Polling screens already refresh on their own interval; refetching
      // every query on each window focus would just double the load.
      refetchOnWindowFocus: false,
      // One retry for network blips and 5xx; a 4xx (bad request, gone,
      // unauthorized) won't change by asking again.
      retry: (failureCount, error) => {
        if (error?.status >= 400 && error?.status < 500) return false;
        return failureCount < 1;
      },
    },
  },
});

export const targetKey = (targetId, ...parts) => ["target", targetId, ...parts];
