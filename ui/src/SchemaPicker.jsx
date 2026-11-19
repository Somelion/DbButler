import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, RefreshCw } from "lucide-react";
import { Checkbox, Popover, popoverStyles } from "./components";
import styles from "./SchemaPicker.module.css";

// Dropdown for a connection's schema allowlist: every schema in the
// database, with a search box and its table count. `value` is null for
// "all schemas" (no allowlist, so schemas added later are watched too) or
// the list of chosen names; ticking every schema turns it back into null.
// [] means nothing is ticked — the caller shouldn't save that.
//
// `loadSchemas` returns { schemas: [{ name, table_count }] }. It runs the
// first time the dropdown opens, and again after a failure. Remount the
// picker (a React key) when what it loads from changes.
//
// `id` and `aria-describedby` go onto the trigger, so it works as a <Field>
// child.
export default function SchemaPicker({ value, onChange, loadSchemas, disabled, id, "aria-describedby": describedBy }) {
  const { t } = useTranslation("connections");
  const [load, setLoad] = useState({ status: "idle" }); // idle | loading | error | done

  const fetchSchemas = async () => {
    setLoad({ status: "loading" });
    try {
      const data = await loadSchemas();
      setLoad({ status: "done", schemas: data.schemas });
    } catch (err) {
      setLoad({ status: "error", error: err.message });
    }
  };

  const summary =
    value === null
      ? t("picker.all")
      : value.length === 0
        ? t("picker.none")
        : t("picker.some", { count: value.length, schemas: value.join(", ") });

  return (
    <Popover
      align="start"
      className={styles.root}
      buttonClassName={`${styles.trigger} ${value?.length === 0 ? styles.triggerInvalid : ""}`}
      buttonProps={{ id, "aria-describedby": describedBy, disabled }}
      panelClassName={styles.panel}
      panelLabel={t("picker.panelLabel")}
      onOpen={() => {
        if (load.status === "idle" || load.status === "error") fetchSchemas();
      }}
      label={
        <>
          <span className={styles.summary}>{summary}</span>
          <ChevronDown size={14} aria-hidden="true" className={styles.chevron} />
        </>
      }
    >
      {() => <SchemaList load={load} value={value} onChange={onChange} onRetry={fetchSchemas} />}
    </Popover>
  );
}

function SchemaList({ load, value, onChange, onRetry }) {
  const { t } = useTranslation("connections");
  const [search, setSearch] = useState("");
  const searchRef = useRef(null);

  // The search box appears once the list has loaded.
  useEffect(() => {
    if (load.status === "done") searchRef.current?.focus();
  }, [load.status]);

  if (load.status === "loading" || load.status === "idle") {
    return <p className={styles.message}>{t("picker.loading")}</p>;
  }
  if (load.status === "error") {
    return (
      <div className={styles.message}>
        <p className={styles.error}>{t("picker.loadFailed", { error: load.error })}</p>
        <button type="button" className="button-secondary button-sm" onClick={onRetry}>
          <RefreshCw size={12} aria-hidden="true" />
          {t("picker.retry")}
        </button>
      </div>
    );
  }

  const names = load.schemas.map((s) => s.name);
  const known = new Set(names);
  // Saved names the database no longer has (dropped or renamed) stay listed,
  // ticked, so they can be unticked.
  const missing = value === null ? [] : value.filter((name) => !known.has(name));
  const rows = [
    ...load.schemas,
    ...missing.map((name) => ({ name, table_count: null })),
  ];
  const selected = new Set(value === null ? names : value);

  const commit = (next) => {
    const all = names.length > 0 && names.every((name) => next.has(name)) && next.size === names.length;
    onChange(all ? null : rows.map((r) => r.name).filter((name) => next.has(name)));
  };
  const toggle = (name) => {
    const next = new Set(selected);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    commit(next);
  };

  const query = search.trim().toLowerCase();
  const visible = query ? rows.filter((r) => r.name.toLowerCase().includes(query)) : rows;

  return (
    <>
      <input
        ref={searchRef}
        type="search"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder={t("picker.search", { count: names.length })}
        aria-label={t("picker.searchAria")}
        className={styles.search}
      />
      <div className={styles.bulkActions}>
        <button type="button" className="button-link" onClick={() => onChange(null)}>
          {t("common:actions.selectAll")}
        </button>
        <button type="button" className="button-link" onClick={() => onChange([])}>
          {t("common:actions.selectNone")}
        </button>
      </div>
      <div className={popoverStyles.divider} />
      <div className={styles.list}>
        {visible.length === 0 && <p className={styles.message}>{t("picker.noMatch")}</p>}
        {visible.map((row) => (
          <Checkbox
            key={row.name}
            checked={selected.has(row.name)}
            onChange={() => toggle(row.name)}
            className={`${popoverStyles.option} ${styles.option}`}
          >
            <span className={styles.name}>{row.name}</span>
            <span className={styles.count}>
              {row.table_count === null ? t("picker.notFound") : t("picker.tables", { count: row.table_count })}
            </span>
          </Checkbox>
        ))}
      </div>
      <div className={popoverStyles.divider} />
      <p className={styles.footnote}>{t("picker.allMeansNew")}</p>
    </>
  );
}
