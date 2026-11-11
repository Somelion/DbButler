import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { dashboardSettingsQuery } from "./queries";
import HealthOverview from "./HealthOverview";
import DiagnoseNow from "./DiagnoseNow";
import Dashboard from "./Dashboard";
import WaitEventBreakdown from "./WaitEventBreakdown";
import DashboardSettingsDropdown from "./DashboardSettingsDropdown";
import styles from "./DashboardScreen.module.css";

export default function DashboardScreen({ targetId }) {
  const queryClient = useQueryClient();
  // A failed settings load just means no Customize button and nothing
  // hidden — the dashboard itself still works.
  const { data: settings } = useQuery(dashboardSettingsQuery());

  const hidden = new Set(settings?.hidden_categories ?? []);

  // Optimistic: flips the checkbox immediately (written straight into the
  // settings cache), then persists in the background. A failed save just
  // leaves the toggle as-is locally rather than surfacing an error for
  // something this low-stakes — it'll retry (successfully or not) the next
  // time the user touches any toggle.
  const toggleCategory = (key) => {
    const next = new Set(hidden);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    const hidden_categories = [...next];
    // An in-flight refetch would otherwise land on top of the optimistic value.
    queryClient.cancelQueries({ queryKey: dashboardSettingsQuery().queryKey });
    queryClient.setQueryData(dashboardSettingsQuery().queryKey, (prev) => ({ ...prev, hidden_categories }));
    api.updateDashboardSettings({ hidden_categories }).catch(() => {});
  };

  return (
    <div className={styles.screen}>
      {settings && (
        <div className={styles.toolbar}>
          <DashboardSettingsDropdown categories={settings.categories} hidden={hidden} onToggle={toggleCategory} />
        </div>
      )}
      <HealthOverview targetId={targetId} hiddenCategories={hidden} />
      <DiagnoseNow targetId={targetId} />
      {!hidden.has("wait_events") && <WaitEventBreakdown targetId={targetId} />}
      {!hidden.has("cache_trend") && <Dashboard targetId={targetId} />}
    </div>
  );
}
