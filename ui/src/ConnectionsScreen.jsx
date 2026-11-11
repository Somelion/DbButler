import { useEffect, useId, useRef, useState } from "react";
import { Plus } from "lucide-react";
import { api } from "./api";
import PgBouncerSettings, { SSL_MODE_OPTIONS } from "./PgBouncerSettings";
import CollapsibleGroup from "./CollapsibleGroup";
import { Alert, Badge, Card, Field, severityMeta, useConfirm } from "./components";
import tone from "./components/tone.module.css";
import styles from "./ConnectionsScreen.module.css";

const EMPTY_FORM = {
  name: "",
  host: "",
  port: "5432",
  dbname: "",
  username: "",
  password: "",
  sslmode: "prefer",
  allowedSchemas: "",
};

// "" (or all-whitespace) means "no filter" — parses a comma-separated list
// into a trimmed, non-empty array, or undefined so the field is simply
// omitted from the payload rather than sent as an empty array.
function parseAllowedSchemas(text) {
  const names = text
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  return names.length > 0 ? names : undefined;
}

function formatAllowedSchemas(list) {
  return list && list.length > 0 ? list.join(", ") : "";
}

// Focuses an inline editor's input when it opens (instead of autoFocus).
function useFocusWhen(active) {
  const ref = useRef(null);
  useEffect(() => {
    if (active) ref.current?.focus();
  }, [active]);
  return ref;
}

export default function ConnectionsScreen({ targets, activeTargetId, onCreated, onRemoved, onUpdated, onSelect }) {
  const [showForm, setShowForm] = useState(targets.length === 0);

  return (
    <div className={styles.screen}>
      {targets.length > 0 && (
        <div className={styles.list}>
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
        <button type="button" className={`button-secondary ${styles.addButton}`} onClick={() => setShowForm(true)}>
          <Plus size={14} aria-hidden="true" />
          Add another connection
        </button>
      )}
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

  const asPayload = () => {
    const { allowedSchemas, ...rest } = form;
    return { ...rest, port: parseInt(form.port, 10) || 5432, allowed_schemas: parseAllowedSchemas(allowedSchemas) };
  };

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

  const handleSubmit = async (e) => {
    e.preventDefault();
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
    <div className={styles.formBlock}>
      {error && <Alert tone="critical">{error}</Alert>}
      <Card title="New connection">
        <form onSubmit={handleSubmit} className={styles.form}>
          <Field label="Nickname">
            <input value={form.name} onChange={set("name")} placeholder="analytics-primary" autoComplete="off" />
          </Field>

          <div className={styles.hostRow}>
            <Field
              label="Host"
              hint={
                <>
                  Database on this machine? Use <b>host.docker.internal</b>, not localhost/127.0.0.1 — DbButler runs
                  in its own container, so that would point at itself.
                </>
              }
            >
              <input value={form.host} onChange={set("host")} placeholder="10.0.4.22" autoComplete="off" />
            </Field>
            <Field label="Port">
              <input
                value={form.port}
                onChange={set("port")}
                placeholder="5432"
                inputMode="numeric"
                autoComplete="off"
              />
            </Field>
          </div>

          <div className={styles.pairRow}>
            <Field label="Database">
              <input value={form.dbname} onChange={set("dbname")} placeholder="analytics" autoComplete="off" />
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

          <div className={styles.pairRow}>
            <Field label="Username">
              <input value={form.username} onChange={set("username")} placeholder="pgdba_monitor" autoComplete="off" />
            </Field>
            <Field label="Password">
              <input type="password" value={form.password} onChange={set("password")} autoComplete="new-password" />
            </Field>
          </div>

          <Field
            label="Schemas to monitor (optional)"
            hint="Comma-separated schema names. Narrows Table Health and every Advisor tab to just these schemas — leave blank to see everything, the default."
          >
            <input
              value={form.allowedSchemas}
              onChange={set("allowedSchemas")}
              placeholder="public, sales (leave blank to watch every schema)"
              autoComplete="off"
            />
          </Field>

          <Alert tone="info" compact>
            <b>pg_monitor</b> is enough to watch a database — DbButler never needs superuser. To also use maintenance
            actions (Vacuum/Analyze/reset stats), the role additionally needs to own the tables, or (PostgreSQL 17+)
            membership in the built-in <b>pg_maintain</b> role.
          </Alert>

          <div className={styles.formActions}>
            <button type="button" className="button-primary" onClick={handleTest} disabled={testing || saving}>
              {testing ? "Testing…" : "Test connection"}
            </button>
            <button type="submit" className="button-secondary" disabled={saving || testing}>
              {saving ? "Saving…" : "Save connection"}
            </button>
            {onCancel && (
              <button type="button" className="button-secondary" onClick={onCancel} disabled={saving || testing}>
                Cancel
              </button>
            )}
          </div>
        </form>
      </Card>

      {testResult && <TestResultBanner result={testResult} />}
    </div>
  );
}

function TestResultBanner({ result }) {
  if (!result.ok) {
    return <Alert tone="critical">{result.message || "Could not connect."}</Alert>;
  }
  return (
    <Alert tone="healthy" title={`Connected — PostgreSQL ${result.pg_version}`}>
      <span className={styles.uptime}>uptime {result.uptime_human}</span>
    </Alert>
  );
}

function ConnectionCard({ target, isActive, onSelect, onRemoved, onUpdated }) {
  const confirm = useConfirm();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [renaming, setRenaming] = useState(false);
  const [nameDraft, setNameDraft] = useState(target.name);
  const [updatingPassword, setUpdatingPassword] = useState(false);
  const [passwordDraft, setPasswordDraft] = useState("");
  const [editingSchemas, setEditingSchemas] = useState(false);
  const [schemasDraft, setSchemasDraft] = useState(() => formatAllowedSchemas(target.allowed_schemas));

  const renameRef = useFocusWhen(renaming);
  const passwordRef = useFocusWhen(updatingPassword);
  const schemasRef = useFocusWhen(editingSchemas);
  const renameId = useId();
  const passwordId = useId();
  const schemasId = useId();

  const handleSaveSchemas = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const updated = await api.updateTarget(target.id, {
        allowed_schemas: parseAllowedSchemas(schemasDraft) || [],
      });
      onUpdated(updated);
      setEditingSchemas(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

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
    const confirmed = await confirm({
      title: "Remove connection?",
      message: `Remove "${target.name}" (${target.host}:${target.port}/${target.dbname})? DbButler stops monitoring it and its collected history is deleted. The database itself is not touched.`,
      confirmLabel: "Remove connection",
      tone: "danger",
    });
    if (!confirmed) return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteTarget(target.id);
      onRemoved();
    } catch (err) {
      setError(err.message);
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

  const handleSaveRename = async (e) => {
    e.preventDefault();
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

  const handleSavePassword = async (e) => {
    e.preventDefault();
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
  const { Icon: StatusIcon } = severityMeta(ok ? "healthy" : "critical");

  return (
    <div className={styles.cardBlock}>
      {error && <Alert tone="critical">{error}</Alert>}
      <Card className={styles.connectionCard} aria-current={isActive ? "true" : undefined}>
        <div className={styles.cardHeader}>
          <div className={styles.identity}>
            {renaming ? (
              <form onSubmit={handleSaveRename} className={styles.inlineForm}>
                <label htmlFor={renameId} className="sr-only">
                  Connection name
                </label>
                <input
                  id={renameId}
                  ref={renameRef}
                  value={nameDraft}
                  onChange={(e) => setNameDraft(e.target.value)}
                  autoComplete="off"
                />
                <button type="submit" className="button-secondary button-sm" disabled={busy}>
                  Save
                </button>
                <button
                  type="button"
                  className="button-secondary button-sm"
                  onClick={() => {
                    setRenaming(false);
                    setNameDraft(target.name);
                  }}
                  disabled={busy}
                >
                  Cancel
                </button>
              </form>
            ) : (
              <div className={styles.nameRow}>
                <h2 className={styles.name}>{target.name}</h2>
                <button
                  type="button"
                  className="button-link"
                  onClick={() => setRenaming(true)}
                  aria-label={`Rename ${target.name}`}
                >
                  Rename
                </button>
                {!target.is_active && <Badge tone="neutral">Paused</Badge>}
              </div>
            )}
            <span className={styles.address}>
              {target.host}:{target.port}/{target.dbname}
            </span>
          </div>

          {isActive ? (
            <Badge tone="info" className={styles.headerAction}>
              Currently viewing
            </Badge>
          ) : (
            <button
              type="button"
              className={`button-secondary button-sm ${styles.headerAction}`}
              onClick={onSelect}
            >
              Switch to this<span className="sr-only">: {target.name}</span>
            </button>
          )}
        </div>

        <div className={`${styles.status} ${ok ? tone.healthy : tone.critical}`}>
          <StatusIcon size={16} className={styles.statusIcon} aria-hidden="true" />
          <div className={styles.statusText}>
            <span className={styles.statusTitle}>
              {ok ? `Connected — PostgreSQL ${target.detected_pg_version}` : "Not connected"}
            </span>
            {!ok && target.last_test_message && (
              <span className={styles.statusMessage}>{target.last_test_message}</span>
            )}
          </div>
        </div>

        {updatingPassword ? (
          <form onSubmit={handleSavePassword} className={styles.inlineForm}>
            <label htmlFor={passwordId} className="sr-only">
              New password for {target.name}
            </label>
            <input
              id={passwordId}
              ref={passwordRef}
              type="password"
              value={passwordDraft}
              onChange={(e) => setPasswordDraft(e.target.value)}
              placeholder="New password"
              autoComplete="new-password"
              className={styles.grow}
            />
            <button type="submit" className="button-secondary" disabled={busy || !passwordDraft}>
              {busy ? "Testing…" : "Save & test"}
            </button>
            <button
              type="button"
              className="button-secondary"
              onClick={() => {
                setUpdatingPassword(false);
                setPasswordDraft("");
              }}
              disabled={busy}
            >
              Cancel
            </button>
          </form>
        ) : (
          <div className={styles.actions}>
            <button type="button" className="button-secondary" onClick={handleRetest} disabled={busy}>
              {busy ? "Working…" : "Test again"}
            </button>
            <button type="button" className="button-secondary" onClick={() => setUpdatingPassword(true)} disabled={busy}>
              Update password
            </button>
            <button type="button" className="button-secondary" onClick={handleTogglePaused} disabled={busy}>
              {target.is_active ? "Pause monitoring" : "Resume monitoring"}
            </button>
            <button type="button" className="button-danger" onClick={handleRemove} disabled={busy}>
              Remove connection
            </button>
          </div>
        )}

        {editingSchemas ? (
          <form onSubmit={handleSaveSchemas} className={styles.inlineForm}>
            <label htmlFor={schemasId} className="sr-only">
              Schemas to monitor (comma-separated)
            </label>
            <input
              id={schemasId}
              ref={schemasRef}
              value={schemasDraft}
              onChange={(e) => setSchemasDraft(e.target.value)}
              placeholder="public, sales (leave blank to watch every schema)"
              autoComplete="off"
              className={styles.grow}
            />
            <button type="submit" className="button-secondary" disabled={busy}>
              {busy ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              className="button-secondary"
              onClick={() => {
                setEditingSchemas(false);
                setSchemasDraft(formatAllowedSchemas(target.allowed_schemas));
              }}
              disabled={busy}
            >
              Cancel
            </button>
          </form>
        ) : (
          <div className={styles.schemasRow}>
            <span className={styles.schemas}>
              {target.allowed_schemas && target.allowed_schemas.length > 0
                ? `Watching: ${target.allowed_schemas.join(", ")}`
                : "Watching all schemas"}
            </span>
            <button
              type="button"
              className="button-link"
              onClick={() => setEditingSchemas(true)}
              title="Choose which schemas Table Health and the Advisor tabs consider"
              aria-label={`Edit monitored schemas for ${target.name}`}
            >
              Edit
            </button>
          </div>
        )}
      </Card>

      <CollapsibleGroup label="PgBouncer (optional)" defaultExpanded={false}>
        <PgBouncerSettings targetId={target.id} targetName={target.name} />
      </CollapsibleGroup>
    </div>
  );
}
