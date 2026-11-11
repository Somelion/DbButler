import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { targetKey } from "./queryClient";
import { configTuningKey } from "./ConfigTuning";
import { Alert, Card, Field, SegmentedControl, Skeleton, Stat, fieldStyles } from "./components";
import styles from "./HardwareProfile.module.css";

const WORKLOAD_OPTIONS = [
  { value: "mixed", label: "Mixed / general use" },
  { value: "oltp", label: "Many small transactions" },
  { value: "analytics", label: "Analytics / reporting" },
  { value: "home", label: "Small home project" },
];

const STORAGE_OPTIONS = [
  { value: "ssd", label: "SSD" },
  { value: "hdd", label: "HDD" },
];

// `onSaved` is optional — ConfigTuning no longer needs it, since saving
// invalidates its query directly.
export default function HardwareProfile({ targetId, onSaved }) {
  const queryClient = useQueryClient();
  const profileKey = targetKey(targetId, "hardware-profile");
  const [editing, setEditing] = useState(false);

  const profileQuery = useQuery({
    queryKey: profileKey,
    queryFn: ({ signal }) => api.getHardwareProfile(targetId, { signal }),
  });
  const profile = profileQuery.data;

  // No saved profile yet: go straight into the form (null = "not set",
  // undefined = still loading).
  const showForm = editing || profile === null;

  const handleSaved = (saved) => {
    queryClient.setQueryData(profileKey, saved);
    queryClient.invalidateQueries({ queryKey: configTuningKey(targetId) });
    setEditing(false);
    onSaved?.(saved);
  };

  return (
    <Card
      title="Hardware Profile"
      actions={
        !showForm &&
        profile && (
          <button type="button" className="button-link" onClick={() => setEditing(true)}>
            Edit
          </button>
        )
      }
    >
      {profileQuery.error && (
        <Alert tone="critical" compact>
          {profileQuery.error.message}
        </Alert>
      )}

      {profileQuery.isPending && <Skeleton lines={2} label="Loading hardware profile…" />}

      {!showForm && profile && (
        <div className={styles.stats}>
          <Stat label="Memory" value={`${(profile.confirmed_ram_mb / 1024).toFixed(1)} GB`} />
          <Stat label="CPU" value={`${profile.confirmed_cpu_cores} cores`} />
          <Stat label="Storage" value={profile.storage_type.toUpperCase()} />
          <Stat
            label="Workload"
            value={WORKLOAD_OPTIONS.find((o) => o.value === profile.workload_type)?.label ?? profile.workload_type}
          />
        </div>
      )}

      {showForm && (
        <HardwareEditor
          targetId={targetId}
          base={profile}
          onSaved={handleSaved}
          onCancel={profile ? () => setEditing(false) : undefined}
        />
      )}
    </Card>
  );
}

// Mounted only while the form is open. Detected RAM/CPU pre-fill the form;
// gcTime 0 drops the result when the form closes, so every Edit re-detects
// (as before), and staleTime Infinity keeps a background refetch from
// changing the pre-fill under the user while the form is open.
function HardwareEditor({ targetId, base, onSaved, onCancel }) {
  const detectionQuery = useQuery({
    queryKey: targetKey(targetId, "hardware-detect"),
    queryFn: ({ signal }) => api.detectHardware(targetId, { signal }),
    staleTime: Infinity,
    gcTime: 0,
  });

  if (detectionQuery.error) {
    return (
      <>
        <Alert tone="critical" compact>
          {detectionQuery.error.message}
        </Alert>
        {onCancel && (
          <div className={styles.actions}>
            <button type="button" className="button-secondary" onClick={onCancel}>
              Cancel
            </button>
          </div>
        )}
      </>
    );
  }
  if (detectionQuery.isPending) return <Skeleton lines={3} label="Detecting hardware…" />;

  return (
    <HardwareForm
      targetId={targetId}
      base={base}
      detection={detectionQuery.data}
      onSaved={onSaved}
      onCancel={onCancel}
    />
  );
}

function HardwareForm({ targetId, base, detection, onSaved, onCancel }) {
  const [form, setForm] = useState(() => ({
    confirmed_ram_mb: base?.confirmed_ram_mb ?? detection.detected_ram_mb,
    confirmed_cpu_cores: base?.confirmed_cpu_cores ?? detection.detected_cpu_cores,
    storage_type: base?.storage_type ?? "ssd",
    workload_type: base?.workload_type ?? "mixed",
  }));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      onSaved(await api.saveHardwareProfile(targetId, form));
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className={styles.form}>
      {error && (
        <Alert tone="critical" compact>
          {error}
        </Alert>
      )}

      {detection.caveat && (
        <Alert tone="info" compact>
          {detection.caveat}
        </Alert>
      )}

      <div className={styles.twoColumns}>
        <Field label="Memory (MB)">
          <input
            inputMode="numeric"
            value={form.confirmed_ram_mb}
            onChange={(e) => setForm({ ...form, confirmed_ram_mb: parseInt(e.target.value, 10) || 0 })}
          />
        </Field>
        <Field label="CPU cores">
          <input
            inputMode="numeric"
            value={form.confirmed_cpu_cores}
            onChange={(e) => setForm({ ...form, confirmed_cpu_cores: parseInt(e.target.value, 10) || 0 })}
          />
        </Field>
      </div>

      <div className={styles.group}>
        <span className={fieldStyles.label} aria-hidden="true">
          Storage type
        </span>
        <SegmentedControl
          label="Storage type"
          options={STORAGE_OPTIONS}
          value={form.storage_type}
          onChange={(storage_type) => setForm({ ...form, storage_type })}
        />
      </div>

      <div className={styles.group}>
        <span className={fieldStyles.label} aria-hidden="true">
          Workload type
        </span>
        <SegmentedControl
          label="Workload type"
          options={WORKLOAD_OPTIONS}
          value={form.workload_type}
          onChange={(workload_type) => setForm({ ...form, workload_type })}
        />
      </div>

      <div className={styles.actions}>
        <button type="submit" className="button-primary" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
        {onCancel && (
          <button type="button" className="button-secondary" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
