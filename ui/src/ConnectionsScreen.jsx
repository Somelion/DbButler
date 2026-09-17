import { useState } from "react";
import { api } from "./api";
import PgBouncerSettings from "./PgBouncerSettings";

const EMPTY_FORM = {
  name: "",
  host: "",
  port: "5432",
  dbname: "",
  username: "",
  password: "",
  sslmode: "prefer",
};

export default function ConnectionsScreen({ targets, activeTargetId, onCreated, onRemoved, onUpdated, onSelect }) {
  const [showForm, setShowForm] = useState(targets.length === 0);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, width: 680 }}>
      {targets.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {targets.map((target) => (
            <ConnectionCard
              key={target.id}
              target={target}
              isActive={target.id === activeTargetId}
              onSelect={() => onSelect(target.id)}
              onRemoved={() => onRemoved(target.id)}
              onUpdated={(updated) => onUpdated(target.id, updated)}
            />
          ))}
        </div>
      )}

      {showForm ? (
        <ConnectionForm
          onSaved={(created) => {
            onCreated(created);
            setShowForm(false);
          }}
          onCancel={targets.length > 0 ? () => setShowForm(false) : undefined}
        />
      ) : (
        <button className="button-secondary" onClick={() => setShowForm(true)} style={{ alignSelf: "flex-start" }}>
          + Add another connection
        </button>
      )}
    </div>
  );
}

function ErrorBanner({ message }) {
  return (
    <div
      style={{
        padding: "12px 16px",
        background: "var(--critical-bg)",
        border: "1px solid var(--critical-border)",
        borderRadius: 12,
        color: "var(--critical-text)",
        fontSize: 13,
      }}
    >
      {message}
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

function ConnectionForm({ onSaved, onCancel }) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const set = (field) => (e) => setForm({ ...form, [field]: e.target.value });

  const asPayload = () => ({ ...form, port: parseInt(form.port, 10) || 5432 });

  const handleTest = async () => {
    setTesting(true);
    setError(null);
    setTestResult(null);
    try {
      const result = await api.testNewConnection(asPayload());
      setTestResult(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const created = await api.createTarget(asPayload());
      onSaved(created);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {error && <ErrorBanner message={error} />}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 16,
          padding: 22,
          background: "var(--card)",
          border: "1px solid var(--border)",
          borderRadius: 14,
        }}
      >
        <Field label="Nickname">
          <input value={form.name} onChange={set("name")} placeholder="analytics-primary" />
        </Field>

        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 12 }}>
          <Field label="Host">
            <input value={form.host} onChange={set("host")} placeholder="10.0.4.22" />
            <span style={{ fontSize: 11, color: "var(--text-muted)", lineHeight: 1.4 }}>
              Database on this machine? Use <b>host.docker.internal</b>, not localhost/127.0.0.1 &mdash;
              PostgreDba runs in its own container, so that would point at itself.
            </span>
          </Field>
          <Field label="Port">
            <input value={form.port} onChange={set("port")} placeholder="5432" />
          </Field>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <Field label="Database">
            <input value={form.dbname} onChange={set("dbname")} placeholder="analytics" />
          </Field>
          <Field label="SSL mode">
            <input value={form.sslmode} onChange={set("sslmode")} placeholder="prefer" />
          </Field>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <Field label="Username">
            <input value={form.username} onChange={set("username")} placeholder="pgdba_monitor" />
          </Field>
          <Field label="Password">
            <input type="password" value={form.password} onChange={set("password")} />
          </Field>
        </div>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "10px 12px",
            background: "var(--accent-tint)",
            borderRadius: 9,
          }}
        >
          <span style={{ fontSize: 12, color: "oklch(35% 0.1 255)", lineHeight: 1.5 }}>
            <b>pg_monitor</b> is enough to watch a database &mdash; PostgreDba never needs superuser. To also
            use maintenance actions (Vacuum/Analyze/reset stats), the role additionally needs to own the
            tables, or (PostgreSQL 17+) membership in the built-in <b>pg_maintain</b> role.
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10, paddingTop: 4 }}>
          <button className="button-primary" onClick={handleTest} disabled={testing || saving}>
            {testing ? "Testing…" : "Test connection"}
          </button>
          <button className="button-secondary" onClick={handleSave} disabled={saving || testing}>
            {saving ? "Saving…" : "Save connection"}
          </button>
          {onCancel && (
            <button className="button-secondary" onClick={onCancel} disabled={saving || testing}>
              Cancel
            </button>
          )}
        </div>
      </div>

      {testResult && <TestResultBanner result={testResult} />}
    </div>
  );
}

function TestResultBanner({ result }) {
  if (!result.ok) {
    return <ErrorBanner message={result.message || "Could not connect."} />;
  }
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "16px 18px",
        background: "var(--healthy-bg)",
        border: "1px solid var(--healthy-border)",
        borderRadius: 14,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
        <span style={{ fontFamily: "Manrope, sans-serif", fontWeight: 650, fontSize: 13.5, color: "var(--healthy-text)" }}>
          Connected &mdash; PostgreSQL {result.pg_version}
        </span>
        <span style={{ fontFamily: "IBM Plex Mono, monospace", fontSize: 12, color: "oklch(38% 0.09 150)" }}>
          uptime {result.uptime_human}
        </span>
      </div>
    </div>
  );
}

function ConnectionCard({ target, isActive, onSelect, onRemoved, onUpdated }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [renaming, setRenaming] = useState(false);
  const [nameDraft, setNameDraft] = useState(target.name);
  const [updatingPassword, setUpdatingPassword] = useState(false);
  const [passwordDraft, setPasswordDraft] = useState("");

  const handleRetest = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.retestTarget(target.id);
      onUpdated({
        detected_pg_version: result.pg_version,
        last_test_ok: result.ok,
        last_test_message: result.message,
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.deleteTarget(target.id);
      onRemoved();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleTogglePaused = async () => {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.updateTarget(target.id, { is_active: !target.is_active });
      onUpdated(updated);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleSaveRename = async () => {
    if (!nameDraft.trim() || nameDraft === target.name) {
      setRenaming(false);
      setNameDraft(target.name);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await api.updateTarget(target.id, { name: nameDraft.trim() });
      onUpdated(updated);
      setRenaming(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleSavePassword = async () => {
    if (!passwordDraft) {
      setUpdatingPassword(false);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await api.updateTarget(target.id, { password: passwordDraft });
      onUpdated(updated);
      setUpdatingPassword(false);
      setPasswordDraft("");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const ok = target.last_test_ok;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {error && <ErrorBanner message={error} />}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 16,
          padding: 22,
          background: "var(--card)",
          border: `1px solid ${isActive ? "var(--accent)" : "var(--border)"}`,
          borderRadius: 14,
        }}
      >
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
            {renaming ? (
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <input
                  value={nameDraft}
                  onChange={(e) => setNameDraft(e.target.value)}
                  autoFocus
                  style={{ fontSize: 15, fontWeight: 650, padding: "3px 6px" }}
                />
                <button className="button-secondary" onClick={handleSaveRename} disabled={busy} style={{ padding: "3px 10px", fontSize: 11.5 }}>
                  Save
                </button>
                <button
                  className="button-secondary"
                  onClick={() => {
                    setRenaming(false);
                    setNameDraft(target.name);
                  }}
                  disabled={busy}
                  style={{ padding: "3px 10px", fontSize: 11.5 }}
                >
                  Cancel
                </button>
              </div>
            ) : (
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontFamily: "Manrope, sans-serif", fontWeight: 650, fontSize: 15, color: "var(--text)" }}>
                  {target.name}
                </span>
                <span
                  onClick={() => setRenaming(true)}
                  title="Rename"
                  style={{ fontSize: 11, color: "var(--accent)", cursor: "pointer" }}
                >
                  Rename
                </span>
                {!target.is_active && (
                  <span
                    style={{
                      fontSize: 10,
                      fontWeight: 700,
                      letterSpacing: "0.02em",
                      textTransform: "uppercase",
                      color: "var(--text-muted)",
                      padding: "2px 7px",
                      borderRadius: 100,
                      background: "var(--row-bg)",
                    }}
                  >
                    Paused
                  </span>
                )}
              </div>
            )}
            <span style={{ fontFamily: "IBM Plex Mono, monospace", fontSize: 12, color: "var(--text-muted)" }}>
              {target.host}:{target.port}/{target.dbname}
            </span>
          </div>

          {isActive ? (
            <span
              style={{
                flexShrink: 0,
                fontSize: 11,
                fontWeight: 650,
                color: "var(--accent)",
                padding: "4px 10px",
                borderRadius: 100,
                background: "var(--accent-tint)",
              }}
            >
              Currently viewing
            </span>
          ) : (
            <button className="button-secondary" onClick={onSelect} style={{ flexShrink: 0, padding: "5px 12px", fontSize: 11.5 }}>
              Switch to this
            </button>
          )}
        </div>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "14px 16px",
            background: ok ? "var(--healthy-bg)" : "var(--critical-bg)",
            border: `1px solid ${ok ? "var(--healthy-border)" : "var(--critical-border)"}`,
            borderRadius: 12,
          }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
            <span
              style={{
                fontFamily: "Manrope, sans-serif",
                fontWeight: 650,
                fontSize: 13.5,
                color: ok ? "var(--healthy-text)" : "var(--critical-text)",
              }}
            >
              {ok ? `Connected — PostgreSQL ${target.detected_pg_version}` : "Not connected"}
            </span>
            {!ok && target.last_test_message && (
              <span style={{ fontSize: 12, color: "var(--critical-text)" }}>{target.last_test_message}</span>
            )}
          </div>
        </div>

        {updatingPassword ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input
              type="password"
              autoFocus
              value={passwordDraft}
              onChange={(e) => setPasswordDraft(e.target.value)}
              placeholder="New password"
              style={{ flex: 1 }}
            />
            <button className="button-secondary" onClick={handleSavePassword} disabled={busy || !passwordDraft}>
              {busy ? "Testing…" : "Save & test"}
            </button>
            <button
              className="button-secondary"
              onClick={() => {
                setUpdatingPassword(false);
                setPasswordDraft("");
              }}
              disabled={busy}
            >
              Cancel
            </button>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <button className="button-secondary" onClick={handleRetest} disabled={busy}>
              {busy ? "Working…" : "Test again"}
            </button>
            <button className="button-secondary" onClick={() => setUpdatingPassword(true)} disabled={busy}>
              Update password
            </button>
            <button className="button-secondary" onClick={handleTogglePaused} disabled={busy}>
              {target.is_active ? "Pause monitoring" : "Resume monitoring"}
            </button>
            <button className="button-danger" onClick={handleRemove} disabled={busy}>
              Remove connection
            </button>
          </div>
        )}
      </div>

      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 16,
          padding: 22,
          background: "var(--card)",
          border: "1px solid var(--border)",
          borderRadius: 14,
        }}
      >
        <PgBouncerSettings targetId={target.id} />
      </div>
    </div>
  );
}
