import { useEffect, useState } from "react";
import { api } from "./api";
import HealthOverview from "./HealthOverview";
import DiagnoseNow from "./DiagnoseNow";
import Dashboard from "./Dashboard";
import WaitEventBreakdown from "./WaitEventBreakdown";
import DashboardSettingsDropdown from "./DashboardSettingsDropdown";

export default function DashboardScreen({ targetId, onNavigate }) {
  const [settings, setSettings] = useState(null);

  useEffect(() => {
    api.getDashboardSettings().then(setSettings).catch(() => {});
  }, []);

  const hidden = new Set(settings?.hidden_categories ?? []);

  // Optimistic: flips the checkbox immediately, then persists in the
  // background. A failed save just leaves the toggle as-is locally rather
  // than surfacing an error for something this low-stakes — it'll retry
  // (successfully or not) the next time the user touches any toggle.
  const toggleCategory = (key) => {
    const next = new Set(hidden);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    const hidden_categories = [...next];
    setSettings((prev) => ({ ...prev, hidden_categories }));
    api.updateDashboardSettings({ hidden_categories }).catch(() => {});
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {settings && (
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <DashboardSettingsDropdown categories={settings.categories} hidden={hidden} onToggle={toggleCategory} />
        </div>
      )}
      <HealthOverview targetId={targetId} hiddenCategories={hidden} onNavigate={onNavigate} />
      <DiagnoseNow targetId={targetId} />
      {!hidden.has("wait_events") && <WaitEventBreakdown targetId={targetId} />}
      {!hidden.has("cache_trend") && <Dashboard targetId={targetId} />}
    </div>
  );
}
