import { useEffect, useId, useRef, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
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
  const { t } = useTranslation("connections");
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
          {t("addAnother")}
        </button>
      )}
    </div>
  );
}

function ConnectionForm({ onSaved, onCancel }) {
  const { t } = useTranslation("connections");
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
      <Card title={t("form.title")}>
        <form onSubmit={handleSubmit} className={styles.form}>
          <Field label={t("form.nickname")}>
            <input value={form.name} onChange={set("name")} placeholder="analytics-primary" autoComplete="off" />
          </Field>

          <div className={styles.hostRow}>
            <Field label={t("form.host")} hint={<Trans t={t} i18nKey="form.hostHint" components={{ b: <b /> }} />}>
              <input value={form.host} onChange={set("host")} placeholder="10.0.4.22" autoComplete="off" />
            </Field>
            <Field label={t("form.port")}>
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
            <Field label={t("form.database")}>
              <input value={form.dbname} onChange={set("dbname")} placeholder="analytics" autoComplete="off" />
            </Field>
            <Field label={t("form.sslMode")}>
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
            <Field label={t("form.username")}>
              <input value={form.username} onChange={set("username")} placeholder="pgdba_monitor" autoComplete="off" />
            </Field>
            <Field label={t("form.password")}>
              <input type="password" value={form.password} onChange={set("password")} autoComplete="new-password" />
            </Field>
          </div>

          <Field label={t("form.schemas")} hint={t("form.schemasHint")}>
            <input
              value={form.allowedSchemas}
              onChange={set("allowedSchemas")}
              placeholder={t("schemasPlaceholder")}
              autoComplete="off"
            />
          </Field>

          <Alert tone="info" compact>
            <Trans t={t} i18nKey="form.rolesNote" components={{ b: <b /> }} />
          </Alert>

          <div className={styles.formActions}>
            <button type="button" className="button-primary" onClick={handleTest} disabled={testing || saving}>
              {testing ? t("testing") : t("testConnection")}
            </button>
            <button type="submit" className="button-secondary" disabled={saving || testing}>
              {saving ? t("common:actions.saving") : t("form.save")}
            </button>
            {onCancel && (
              <button type="button" className="button-secondary" onClick={onCancel} disabled={saving || testing}>
                {t("common:actions.cancel")}
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
  const { t } = useTranslation("connections");
  if (!result.ok) {
    return <Alert tone="critical">{result.message || t("couldNotConnect")}</Alert>;
  }
  return (
    <Alert tone="healthy" title={t("connectedPg", { version: result.pg_version })}>
      <span className={styles.uptime}>{t("form.uptime", { uptime: result.uptime_human })}</span>
    </Alert>
  );
}

function ConnectionCard({ target, isActive, onSelect, onRemoved, onUpdated }) {
  const { t } = useTranslation("connections");
  const confirm = useConfirm();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [renaming, setRenaming] = useState(false);
  const [nameDraft, setNameDraft] = useState(target.name);
  const [updatingPassword, setUpdatingPassword] = useState(false);
  const [usernameDraft, setUsernameDraft] = useState(target.username);
  const [passwordDraft, setPasswordDraft] = useState("");
  const [editingSchemas, setEditingSchemas] = useState(false);
  const [schemasDraft, setSchemasDraft] = useState(() => formatAllowedSchemas(target.allowed_schemas));

  const renameRef = useFocusWhen(renaming);
  const passwordRef = useFocusWhen(updatingPassword);
  const schemasRef = useFocusWhen(editingSchemas);
  const renameId = useId();
  const usernameId = useId();
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
      title: t("card.removeTitle"),
      message: t("card.removeMessage", {
        name: target.name,
        address: `${target.host}:${target.port}/${target.dbname}`,
      }),
      confirmLabel: t("card.remove"),
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

  const closeLoginForm = () => {
    setUpdatingPassword(false);
    setUsernameDraft(target.username);
    setPasswordDraft("");
  };

  // Changing the user and/or the password: the backend tests the new login
  // first and keeps the old one if it fails. A blank password keeps the
  // stored one.
  const handleSaveLogin = async (e) => {
    e.preventDefault();
    const username = usernameDraft.trim();
    const changes = {};
    if (username && username !== target.username) changes.username = username;
    if (passwordDraft) changes.password = passwordDraft;
    if (Object.keys(changes).length === 0) {
      closeLoginForm();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await api.updateTarget(target.id, changes);
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
                  {t("card.nameLabel")}
                </label>
                <input
                  id={renameId}
                  ref={renameRef}
                  value={nameDraft}
                  onChange={(e) => setNameDraft(e.target.value)}
                  autoComplete="off"
                />
                <button type="submit" className="button-secondary button-sm" disabled={busy}>
                  {t("common:actions.save")}
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
                  {t("common:actions.cancel")}
                </button>
              </form>
            ) : (
              <div className={styles.nameRow}>
                <h2 className={styles.name}>{target.name}</h2>
                <button
                  type="button"
                  className="button-link"
                  onClick={() => setRenaming(true)}
                  aria-label={t("card.renameAria", { name: target.name })}
                >
                  {t("card.rename")}
                </button>
                {!target.is_active && <Badge tone="neutral">{t("card.paused")}</Badge>}
              </div>
            )}
            <span className={styles.address}>
              {target.username}@{target.host}:{target.port}/{target.dbname}
            </span>
          </div>

          {isActive ? (
            <Badge tone="info" className={styles.headerAction}>
              {t("card.currentlyViewing")}
            </Badge>
          ) : (
            <button
              type="button"
              className={`button-secondary button-sm ${styles.headerAction}`}
              onClick={onSelect}
            >
              {t("card.switchTo")}
              <span className="sr-only">: {target.name}</span>
            </button>
          )}
        </div>

        <div className={`${styles.status} ${ok ? tone.healthy : tone.critical}`}>
          <StatusIcon size={16} className={styles.statusIcon} aria-hidden="true" />
          <div className={styles.statusText}>
            <span className={styles.statusTitle}>
              {ok ? t("connectedPg", { version: target.detected_pg_version }) : t("common:nav.notConnected")}
            </span>
            {!ok && target.last_test_message && (
              <span className={styles.statusMessage}>{target.last_test_message}</span>
            )}
          </div>
        </div>

        {updatingPassword ? (
          <form onSubmit={handleSaveLogin} className={styles.inlineForm}>
            <label htmlFor={usernameId} className="sr-only">
              {t("card.usernameLabel", { name: target.name })}
            </label>
            <input
              id={usernameId}
              ref={passwordRef}
              value={usernameDraft}
              onChange={(e) => setUsernameDraft(e.target.value)}
              placeholder={t("form.username")}
              autoComplete="off"
              className={styles.grow}
            />
            <label htmlFor={passwordId} className="sr-only">
              {t("card.newPasswordLabel", { name: target.name })}
            </label>
            <input
              id={passwordId}
              type="password"
              value={passwordDraft}
              onChange={(e) => setPasswordDraft(e.target.value)}
              placeholder={t("card.newPasswordKeep")}
              autoComplete="new-password"
              className={styles.grow}
            />
            <button
              type="submit"
              className="button-secondary"
              disabled={busy || !usernameDraft.trim() || (usernameDraft.trim() === target.username && !passwordDraft)}
            >
              {busy ? t("testing") : t("card.saveAndTest")}
            </button>
            <button type="button" className="button-secondary" onClick={closeLoginForm} disabled={busy}>
              {t("common:actions.cancel")}
            </button>
          </form>
        ) : (
          <div className={styles.actions}>
            <button type="button" className="button-secondary" onClick={handleRetest} disabled={busy}>
              {busy ? t("card.working") : t("testAgain")}
            </button>
            <button type="button" className="button-secondary" onClick={() => setUpdatingPassword(true)} disabled={busy}>
              {t("card.changeLogin")}
            </button>
            <button type="button" className="button-secondary" onClick={handleTogglePaused} disabled={busy}>
              {target.is_active ? t("card.pause") : t("card.resume")}
            </button>
            <button type="button" className="button-danger" onClick={handleRemove} disabled={busy}>
              {t("card.remove")}
            </button>
          </div>
        )}

        {editingSchemas ? (
          <form onSubmit={handleSaveSchemas} className={styles.inlineForm}>
            <label htmlFor={schemasId} className="sr-only">
              {t("card.schemasLabel")}
            </label>
            <input
              id={schemasId}
              ref={schemasRef}
              value={schemasDraft}
              onChange={(e) => setSchemasDraft(e.target.value)}
              placeholder={t("schemasPlaceholder")}
              autoComplete="off"
              className={styles.grow}
            />
            <button type="submit" className="button-secondary" disabled={busy}>
              {busy ? t("common:actions.saving") : t("common:actions.save")}
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
              {t("common:actions.cancel")}
            </button>
          </form>
        ) : (
          <div className={styles.schemasRow}>
            <span className={styles.schemas}>
              {target.allowed_schemas && target.allowed_schemas.length > 0
                ? t("card.watching", { schemas: target.allowed_schemas.join(", ") })
                : t("card.watchingAll")}
            </span>
            <button
              type="button"
              className="button-link"
              onClick={() => setEditingSchemas(true)}
              title={t("card.editSchemasTitle")}
              aria-label={t("card.editSchemasAria", { name: target.name })}
            >
              {t("card.edit")}
            </button>
          </div>
        )}
      </Card>

      <CollapsibleGroup label={t("card.pgbouncerGroup")} defaultExpanded={false}>
        <PgBouncerSettings targetId={target.id} targetName={target.name} />
      </CollapsibleGroup>
    </div>
  );
}
