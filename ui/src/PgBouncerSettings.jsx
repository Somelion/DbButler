import { useEffect, useState } from "react";
import { api } from "./api";

const EMPTY_FORM = { host: "", port: "6432", username: "", password: "", sslmode: "prefer" };

function Field({ label, children }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text)" }}>{label}</span>
      {children}
    </div>
  );
}

// Optional, separate from the target's own Postgres connection above —
// PgBouncer's admin console lives at its own host/port, reached over the
// same wire protocol but via its "pgbouncer" magic database. Surfaces pool
// utilization on the Dashboard's Pooler tile once configured
// (routers/dashboard.py); most targets never set this up at all.
export default function PgBouncerSettings({ targetId }) {
  const [connection, setConnection] = useState(undefined); // undefined = loading, null = not configured
  const [error, setError] = useState(null);

  const load = () => {
    api
      .getPgBouncerConnection(targetId)
      .then(setConnection)
      .catch((err) => setError(err.message));
  };

  useEffect(() => {
    setConnection(undefined);
    setError(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetId]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <span style={{ fontFamily: "Manrope, sans-serif", fontWeight: 650, fontSize: 13.5, color: "var(--text)" }}>
        PgBouncer (optional)
      </span>
      <span style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>
        If this database sits behind PgBouncer, connect to its admin console to see pool utilization
        on the Dashboard. A user in PgBouncer's <b>stats_users</b> list is enough — this never runs
        PAUSE/KILL/RELOAD.
      </span>
      {error && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{error}</span>}
      {connection === undefined ? null : connection === null ? (
        <PgBouncerForm targetId={targetId} onSaved={load} />
      ) : (
        <PgBouncerStatus targetId={targetId} connection={connection} onChanged={load} setError={setError} />
      )}
    </div>
  );
}

function PgBouncerForm({ targetId, onSaved }) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const set = (field) => (e) => setForm({ ...form, [field]: e.target.value });
  const asPayload = () => ({ ...form, port: parseInt(form.port, 10) || 6432 });

  const handleTest = async () => {
    setTesting(true);
    setError(null);
    setTestResult(null);
    try {
      setTestResult(await api.testNewPgBouncerConnection(targetId, asPayload()));
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
      await api.savePgBouncerConnection(targetId, asPayload());
      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {error && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{error}</span>}
      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 12 }}>
        <Field label="Host">
          <input value={form.host} onChange={set("host")} placeholder="10.0.4.22" />
        </Field>
        <Field label="Port">
          <input value={form.port} onChange={set("port")} placeholder="6432" />
        </Field>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
        <Field label="Username">
          <input value={form.username} onChange={set("username")} placeholder="pgbouncer_stats" />
        </Field>
        <Field label="Password">
          <input type="password" value={form.password} onChange={set("password")} />
        </Field>
        <Field label="SSL mode">
          <input value={form.sslmode} onChange={set("sslmode")} placeholder="prefer" />
        </Field>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button className="button-secondary" onClick={handleTest} disabled={testing || saving}>
          {testing ? "Testing…" : "Test connection"}
        </button>
        <button className="button-primary" onClick={handleSave} disabled={saving || testing}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
      {testResult && (
        <span style={{ fontSize: 12, color: testResult.ok ? "var(--healthy-text)" : "var(--critical-text)" }}>
          {testResult.ok ? `Connected — ${testResult.version}` : testResult.message || "Could not connect."}
        </span>
      )}
    </div>
  );
}

function PgBouncerStatus({ targetId, connection, onChanged, setError }) {
  const [busy, setBusy] = useState(false);
  const ok = connection.last_test_ok;

  const handleRetest = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.retestPgBouncerConnection(targetId);
      onChanged();
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
      await api.deletePgBouncerConnection(targetId);
      onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "12px 14px",
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
              fontSize: 13,
              color: ok ? "var(--healthy-text)" : "var(--critical-text)",
            }}
          >
            {ok ? "Connected" : "Not connected"} — {connection.host}:{connection.port}
          </span>
          {!ok && connection.last_test_message && (
            <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{connection.last_test_message}</span>
          )}
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button className="button-secondary" onClick={handleRetest} disabled={busy}>
          {busy ? "Testing…" : "Test again"}
        </button>
        <button className="button-danger" onClick={handleRemove} disabled={busy}>
          Remove
        </button>
      </div>
    </div>
  );
}
