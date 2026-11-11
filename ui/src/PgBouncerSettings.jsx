import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { targetKey } from "./queryClient";
import { Alert, Field, Skeleton, severityMeta, useConfirm } from "./components";
import tone from "./components/tone.module.css";
import styles from "./PgBouncerSettings.module.css";

// libpq's sslmode values, weakest to strictest. Shared with the main
// connection form in ConnectionsScreen.
export const SSL_MODE_OPTIONS = ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"];

const EMPTY_FORM = { host: "", port: "6432", username: "", password: "", sslmode: "prefer" };

// Optional, separate from the target's own Postgres connection above —
// PgBouncer's admin console lives at its own host/port, reached over the
// same wire protocol but via its "pgbouncer" magic database. Surfaces pool
// utilization on the Dashboard's Pooler tile once configured
// (routers/dashboard.py); most targets never set this up at all.
//
// `targetName` is optional and only used to name the database in the
// remove confirmation.
export default function PgBouncerSettings({ targetId, targetName }) {
  const queryClient = useQueryClient();
  const queryKey = targetKey(targetId, "pgbouncer");
  const [actionError, setActionError] = useState(null);

  // data: null = not configured, otherwise the saved connection.
  const { data: connection, error, isPending } = useQuery({
    queryKey,
    queryFn: ({ signal }) => api.getPgBouncerConnection(targetId, { signal }),
  });

  const reload = () => queryClient.invalidateQueries({ queryKey });

  const shownError = actionError ?? error?.message;

  return (
    <div className={styles.root}>
      <h3 className={styles.title}>PgBouncer (optional)</h3>
      <p className={styles.description}>
        If this database sits behind PgBouncer, connect to its admin console to see pool utilization on the
        Dashboard. A user in PgBouncer's <b>stats_users</b> list is enough — this never runs PAUSE/KILL/RELOAD.
      </p>
      {shownError && (
        <Alert tone="critical" compact>
          {shownError}
        </Alert>
      )}
      {isPending ? (
        <Skeleton lines={2} label="Loading PgBouncer connection…" />
      ) : connection === null ? (
        <PgBouncerForm targetId={targetId} onSaved={reload} />
      ) : connection ? (
        <PgBouncerStatus
          targetId={targetId}
          targetName={targetName}
          connection={connection}
          onChanged={reload}
          setError={setActionError}
        />
      ) : null}
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

  const handleSubmit = async (e) => {
    e.preventDefault();
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
    <form onSubmit={handleSubmit} className={styles.form}>
      {error && (
        <Alert tone="critical" compact>
          {error}
        </Alert>
      )}
      <div className={styles.hostRow}>
        <Field label="Host">
          <input value={form.host} onChange={set("host")} placeholder="10.0.4.22" autoComplete="off" />
        </Field>
        <Field label="Port">
          <input value={form.port} onChange={set("port")} placeholder="6432" inputMode="numeric" autoComplete="off" />
        </Field>
      </div>
      <div className={styles.credentialsRow}>
        <Field label="Username">
          <input value={form.username} onChange={set("username")} placeholder="pgbouncer_stats" autoComplete="off" />
        </Field>
        <Field label="Password">
          <input type="password" value={form.password} onChange={set("password")} autoComplete="new-password" />
        </Field>
        <Field label="SSL mode">
          <select value={form.sslmode} onChange={set("sslmode")}>
            {SSL_MODE_OPTIONS.map((mode) => (
              <option key={mode} value={mode}>
                {mode}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className={styles.actions}>
        <button type="button" className="button-secondary" onClick={handleTest} disabled={testing || saving}>
          {testing ? "Testing…" : "Test connection"}
        </button>
        <button type="submit" className="button-primary" disabled={saving || testing}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
      {testResult && (
        <Alert tone={testResult.ok ? "healthy" : "critical"} compact>
          {testResult.ok ? `Connected — ${testResult.version}` : testResult.message || "Could not connect."}
        </Alert>
      )}
    </form>
  );
}

function PgBouncerStatus({ targetId, targetName, connection, onChanged, setError }) {
  const confirm = useConfirm();
  const [busy, setBusy] = useState(false);
  const ok = connection.last_test_ok;
  const { Icon } = severityMeta(ok ? "healthy" : "critical");

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
    const address = `${connection.host}:${connection.port}`;
    const confirmed = await confirm({
      title: "Remove PgBouncer?",
      message: targetName
        ? `Stop reading pool stats from PgBouncer at ${address} for "${targetName}"? The Dashboard's Pooler tile goes back to unconfigured.`
        : `Stop reading pool stats from PgBouncer at ${address}? The Dashboard's Pooler tile goes back to unconfigured.`,
      confirmLabel: "Remove",
      tone: "danger",
    });
    if (!confirmed) return;
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
    <div className={styles.statusBlock}>
      <div className={`${styles.status} ${ok ? tone.healthy : tone.critical}`}>
        <Icon size={16} className={styles.statusIcon} aria-hidden="true" />
        <div className={styles.statusText}>
          <span className={styles.statusTitle}>
            {ok ? "Connected" : "Not connected"} — {connection.host}:{connection.port}
          </span>
          {!ok && connection.last_test_message && (
            <span className={styles.statusMessage}>{connection.last_test_message}</span>
          )}
        </div>
      </div>
      <div className={styles.actions}>
        <button type="button" className="button-secondary" onClick={handleRetest} disabled={busy}>
          {busy ? "Testing…" : "Test again"}
        </button>
        <button type="button" className="button-danger" onClick={handleRemove} disabled={busy}>
          Remove
        </button>
      </div>
    </div>
  );
}
