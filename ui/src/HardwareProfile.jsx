import { useEffect, useState } from "react";
import { api } from "./api";

const WORKLOAD_OPTIONS = [
  { value: "mixed", label: "Mixed / general use" },
  { value: "oltp", label: "Many small transactions" },
  { value: "analytics", label: "Analytics / reporting" },
  { value: "home", label: "Small home project" },
];

export default function HardwareProfile({ targetId, onSaved }) {
  const [profile, setProfile] = useState(null);
  const [detection, setDetection] = useState(null);
  const [form, setForm] = useState(null);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    api
      .getHardwareProfile(targetId)
      .then((existing) => {
        setProfile(existing);
        if (!existing) startEdit(existing);
      })
      .catch((err) => setError(err.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetId]);

  const startEdit = (base) => {
    api
      .detectHardware(targetId)
      .then((d) => {
        setDetection(d);
        setForm({
          confirmed_ram_mb: base?.confirmed_ram_mb ?? d.detected_ram_mb,
          confirmed_cpu_cores: base?.confirmed_cpu_cores ?? d.detected_cpu_cores,
          storage_type: base?.storage_type ?? "ssd",
          workload_type: base?.workload_type ?? "mixed",
        });
        setEditing(true);
      })
      .catch((err) => setError(err.message));
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const saved = await api.saveHardwareProfile(targetId, form);
      setProfile(saved);
      setEditing(false);
      onSaved?.(saved);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        padding: "18px 20px",
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: 14,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)" }}>Hardware Profile</span>
        {!editing && profile && (
          <span
            onClick={() => startEdit(profile)}
            style={{ fontSize: 12, fontWeight: 600, color: "var(--accent)", cursor: "pointer" }}
          >
            Edit
          </span>
        )}
      </div>

      {error && (
        <div
          style={{
            padding: "10px 12px",
            background: "var(--critical-bg)",
            border: "1px solid var(--critical-border)",
            borderRadius: 9,
            color: "var(--critical-text)",
            fontSize: 12.5,
          }}
        >
          {error}
        </div>
      )}

      {!editing && profile && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 12 }}>
          <Stat label="Memory" value={`${(profile.confirmed_ram_mb / 1024).toFixed(1)} GB`} />
          <Stat label="CPU" value={`${profile.confirmed_cpu_cores} cores`} />
          <Stat label="Storage" value={profile.storage_type.toUpperCase()} />
          <Stat
            label="Workload"
            value={WORKLOAD_OPTIONS.find((o) => o.value === profile.workload_type)?.label ?? profile.workload_type}
          />
        </div>
      )}

      {editing && form && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {detection && (
            <div style={{ padding: "10px 12px", background: "var(--accent-tint)", borderRadius: 9 }}>
              <span style={{ fontSize: 12, color: "oklch(35% 0.1 255)", lineHeight: 1.5 }}>{detection.caveat}</span>
            </div>
          )}

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <Field label="Memory (MB)">
              <input
                value={form.confirmed_ram_mb}
                onChange={(e) => setForm({ ...form, confirmed_ram_mb: parseInt(e.target.value, 10) || 0 })}
              />
            </Field>
            <Field label="CPU cores">
              <input
                value={form.confirmed_cpu_cores}
                onChange={(e) => setForm({ ...form, confirmed_cpu_cores: parseInt(e.target.value, 10) || 0 })}
              />
            </Field>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text)" }}>Storage type</span>
            <div style={{ display: "flex", gap: 8 }}>
              {["ssd", "hdd"].map((s) => (
                <Pill
                  key={s}
                  active={form.storage_type === s}
                  label={s.toUpperCase()}
                  onClick={() => setForm({ ...form, storage_type: s })}
                />
              ))}
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text)" }}>Workload type</span>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {WORKLOAD_OPTIONS.map((o) => (
                <Pill
                  key={o.value}
                  active={form.workload_type === o.value}
                  label={o.label}
                  onClick={() => setForm({ ...form, workload_type: o.value })}
                />
              ))}
            </div>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <button className="button-primary" onClick={handleSave} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </button>
            {profile && (
              <button className="button-secondary" onClick={() => setEditing(false)} disabled={saving}>
                Cancel
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      <span style={{ fontFamily: "IBM Plex Mono, monospace", fontSize: 18, fontWeight: 600, color: "var(--text)" }}>
        {value}
      </span>
      <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>{label}</span>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text)" }}>{label}</span>
      {children}
    </div>
  );
}

function Pill({ label, active, onClick }) {
  return (
    <span
      onClick={onClick}
      style={{
        padding: "7px 14px",
        fontSize: 12.5,
        fontWeight: active ? 650 : 600,
        color: active ? "white" : "var(--text-secondary)",
        background: active ? "var(--accent)" : "transparent",
        border: active ? "none" : "1px solid var(--border)",
        borderRadius: 100,
        cursor: "pointer",
      }}
    >
      {label}
    </span>
  );
}
