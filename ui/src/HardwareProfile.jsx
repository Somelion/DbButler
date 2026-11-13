import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "./api";
import { targetKey } from "./queryClient";
import { configTuningKey } from "./ConfigTuning";
import { Alert, Card, Field, SegmentedControl, Skeleton, Stat, fieldStyles } from "./components";
import styles from "./HardwareProfile.module.css";

// Labels are translated at render from hardware.workloads.<value>.
const WORKLOAD_VALUES = ["mixed", "oltp", "analytics", "home"];
const workloadOptions = (t) =>
  WORKLOAD_VALUES.map((value) => ({ value, label: t(`hardware.workloads.${value}`) }));

const STORAGE_OPTIONS = [
  { value: "ssd", label: "SSD" },
  { value: "hdd", label: "HDD" },
];

// `onSaved` is optional — ConfigTuning no longer needs it, since saving
// invalidates its query directly.
export default function HardwareProfile({ targetId, onSaved }) {
  const { t } = useTranslation("configTuning");
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
      title={t("hardware.title")}
      actions={
        !showForm &&
        profile && (
          <button type="button" className="button-link" onClick={() => setEditing(true)}>
            {t("hardware.edit")}
          </button>
        )
      }
    >
      {profileQuery.error && (
        <Alert tone="critical" compact>
          {profileQuery.error.message}
        </Alert>
      )}

      {profileQuery.isPending && <Skeleton lines={2} label={t("hardware.loading")} />}

      {!showForm && profile && (
        <div className={styles.stats}>
          <Stat label={t("hardware.memory")} value={`${(profile.confirmed_ram_mb / 1024).toFixed(1)} GB`} />
          <Stat label={t("hardware.cpu")} value={t("hardware.cores", { count: profile.confirmed_cpu_cores })} />
          <Stat label={t("hardware.storage")} value={profile.storage_type.toUpperCase()} />
          <Stat
            label={t("hardware.workload")}
            value={
              workloadOptions(t).find((o) => o.value === profile.workload_type)?.label ?? profile.workload_type
            }
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
  const { t } = useTranslation("configTuning");
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
              {t("common:actions.cancel")}
            </button>
          </div>
        )}
      </>
    );
  }
  if (detectionQuery.isPending) return <Skeleton lines={3} label={t("hardware.detecting")} />;

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
  const { t } = useTranslation("configTuning");
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
        <Field label={t("hardware.memoryMb")}>
          <input
            inputMode="numeric"
            value={form.confirmed_ram_mb}
            onChange={(e) => setForm({ ...form, confirmed_ram_mb: parseInt(e.target.value, 10) || 0 })}
          />
        </Field>
        <Field label={t("hardware.cpuCores")}>
          <input
            inputMode="numeric"
            value={form.confirmed_cpu_cores}
            onChange={(e) => setForm({ ...form, confirmed_cpu_cores: parseInt(e.target.value, 10) || 0 })}
          />
        </Field>
      </div>

      <div className={styles.group}>
        <span className={fieldStyles.label} aria-hidden="true">
          {t("hardware.storageType")}
        </span>
        <SegmentedControl
          label={t("hardware.storageType")}
          options={STORAGE_OPTIONS}
          value={form.storage_type}
          onChange={(storage_type) => setForm({ ...form, storage_type })}
        />
      </div>

      <div className={styles.group}>
        <span className={fieldStyles.label} aria-hidden="true">
          {t("hardware.workloadType")}
        </span>
        <SegmentedControl
          label={t("hardware.workloadType")}
          options={workloadOptions(t)}
          value={form.workload_type}
          onChange={(workload_type) => setForm({ ...form, workload_type })}
        />
      </div>

      <div className={styles.actions}>
        <button type="submit" className="button-primary" disabled={saving}>
          {saving ? t("common:actions.saving") : t("common:actions.save")}
        </button>
        {onCancel && (
          <button type="button" className="button-secondary" onClick={onCancel} disabled={saving}>
            {t("common:actions.cancel")}
          </button>
        )}
      </div>
    </form>
  );
}
