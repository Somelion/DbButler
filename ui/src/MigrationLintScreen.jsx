import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Trans, useTranslation } from "react-i18next";
import { FileCode } from "lucide-react";
import styles from "./MigrationLintScreen.module.css";
import tableStyles from "./components/Table.module.css";
import { api } from "./api";
import FindingCard from "./FindingCard";
import { formatRelativeTime } from "./format";
import { Alert, Badge, Card, Checkbox, EmptyState, Field, SeverityBadge } from "./components";

const DRAFT_KEY = "dbbutler.migrationLint.draft";

// The example's comment line is translated when the example is loaded
// (exampleSql below); the SQL itself stays as it is.
const EXAMPLE_SQL = `ALTER TABLE orders ADD COLUMN reference uuid DEFAULT gen_random_uuid();
CREATE INDEX orders_reference_idx ON orders (reference);
ALTER TABLE orders ADD CONSTRAINT orders_customer_fk
    FOREIGN KEY (customer_id) REFERENCES customers (id);
`;

const exampleSql = (comment) => `-- ${comment}\n${EXAMPLE_SQL}`;

// What a lock mode stops, as a badge: the text carries the meaning, the
// tone repeats it. label is a translation key, translated at render.
const BLOCKS = {
  "reads and writes": { tone: "critical", label: "blocks.readsAndWrites" },
  writes: { tone: "attention", label: "blocks.writes" },
  nothing: { tone: "healthy", label: "blocks.nothing" },
};

function readDraft() {
  try {
    return sessionStorage.getItem(DRAFT_KEY) ?? "";
  } catch {
    return "";
  }
}

function saveDraft(text) {
  try {
    sessionStorage.setItem(DRAFT_KEY, text);
  } catch {
    // Storage unavailable (private mode): the draft just isn't kept.
  }
}

function LockList({ locks }) {
  const { t } = useTranslation("migrationLint");
  if (locks.length === 0) return <span className={styles.muted}>—</span>;
  return (
    <ul className={styles.locks}>
      {locks.map((lock, i) => {
        const blocks = BLOCKS[lock.blocks] ?? BLOCKS.nothing;
        return (
          // A static, never-reordered list: the position is a stable key.
          <li key={i}>
            <Trans
              t={t}
              i18nKey="lockOn"
              values={{ mode: lock.mode, relation: lock.relation }}
              components={{ code: <code /> }}
            />
            {lock.via_index && <span className={styles.muted}> {t("viaIndex")}</span>}{" "}
            <Badge tone={blocks.tone}>{t(blocks.label)}</Badge>
          </li>
        );
      })}
    </ul>
  );
}

function StatementsTable({ statements, live }) {
  const { t } = useTranslation("migrationLint");
  return (
    <div className={tableStyles.wrap}>
      <table className={tableStyles.table}>
        <caption className="sr-only">{t("table.caption")}</caption>
        <thead>
          <tr>
            <th scope="col" className={`${tableStyles.th} ${tableStyles.numeric}`}>{t("table.line")}</th>
            <th scope="col" className={tableStyles.th}>{t("table.statement")}</th>
            <th scope="col" className={tableStyles.th}>{t("table.locks")}</th>
            <th scope="col" className={tableStyles.th}>{t("table.howLong")}</th>
            {live && <th scope="col" className={tableStyles.th}>{t("table.onDatabaseNow")}</th>}
          </tr>
        </thead>
        <tbody>
          {statements.map((stmt) => (
            <tr key={stmt.index} className={tableStyles.row}>
              <td className={`${tableStyles.td} ${tableStyles.numeric}`}>{stmt.line}</td>
              <td className={`${tableStyles.td} ${styles.statementCell}`}>
                <span className={styles.kind}>{stmt.kind}</span>
                {stmt.in_transaction && <Badge tone="neutral">{t("table.inTransaction")}</Badge>}
                <code className={styles.sql}>{stmt.sql}</code>
                {stmt.notes.map((note) => (
                  <p key={note} className={styles.note}>
                    {note}
                  </p>
                ))}
              </td>
              <td className={tableStyles.td}>
                <LockList locks={stmt.locks} />
              </td>
              <td className={`${tableStyles.td} ${styles.effectCell}`}>{stmt.effect_label ?? "—"}</td>
              {live && (
                <td className={`${tableStyles.td} ${styles.impactCell}`}>
                  {stmt.impacts.length === 0 ? (
                    <span className={styles.muted}>—</span>
                  ) : (
                    stmt.impacts.map((impact, i) => (
                      <div key={i} className={styles.impact}>
                        <SeverityBadge severity={impact.severity} />
                        <p>{impact.text}</p>
                      </div>
                    ))
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Migration Linter: paste a migration, see what each statement locks, for
// how long, what's risky about it and — against the connected database —
// what it would do there right now. Nothing pasted here is executed.
export default function MigrationLintScreen({ targetId, target }) {
  const { t } = useTranslation("migrationLint");
  const [sql, setSql] = useState(readDraft);
  const [useLive, setUseLive] = useState(true);
  const lint = useMutation({ mutationFn: ({ text, live }) => api.lintMigration(text, live ? targetId : null) });
  const result = lint.data;
  // Without a name, the "…Unnamed" variants say "this database".
  const targetName = target?.name ?? null;

  const handleChange = (event) => {
    setSql(event.target.value);
    saveDraft(event.target.value);
  };

  const handleSubmit = (event) => {
    event.preventDefault();
    if (sql.trim()) lint.mutate({ text: sql, live: useLive });
  };

  const counts = result
    ? result.findings.reduce((acc, f) => ({ ...acc, [f.severity]: (acc[f.severity] ?? 0) + 1 }), {})
    : {};

  return (
    <div className={styles.screen}>
      <Card
        title={t("form.title")}
        description={t("form.description")}
        actions={
          <button
            type="button"
            className="button-link"
            onClick={() => {
              const example = exampleSql(t("exampleComment"));
              setSql(example);
              saveDraft(example);
            }}
          >
            {t("form.loadExample")}
          </button>
        }
      >
        <form className={styles.form} onSubmit={handleSubmit}>
          <Field label="SQL" hint={t("form.sqlHint")}>
            <textarea
              className={styles.editor}
              value={sql}
              onChange={handleChange}
              rows={14}
              spellCheck={false}
              placeholder="ALTER TABLE orders ADD COLUMN …"
            />
          </Field>
          <div className={styles.runRow}>
            <Checkbox checked={useLive} onChange={(e) => setUseLive(e.target.checked)}>
              {targetName
                ? t("form.predictImpact", { target: targetName })
                : t("form.predictImpactUnnamed")}
            </Checkbox>
            <button type="submit" className="button-primary" disabled={!sql.trim() || lint.isPending}>
              <FileCode size={14} aria-hidden="true" />
              {lint.isPending ? t("form.checking") : t("form.check")}
            </button>
          </div>
        </form>
      </Card>

      {lint.error && <Alert tone="critical">{lint.error.message}</Alert>}

      {result?.parse_error && (
        <Alert
          tone="critical"
          title={t("syntaxError", { line: result.parse_error.line, column: result.parse_error.column })}
        >
          {result.parse_error.message}
        </Alert>
      )}

      {result && !result.parse_error && (
        <>
          <div className={styles.summary} role="status">
            <SeverityBadge severity={result.overall_severity} />
            <span>
              {t("summary.statements", { count: result.statements.length })} ·{" "}
              {result.findings.length === 0
                ? t("summary.noRiskyPatterns")
                : t("summary.counts", {
                    critical: counts.critical ?? 0,
                    attention: counts.attention ?? 0,
                    info: counts.info ?? 0,
                  })}
            </span>
            {result.live && (
              <span className={styles.muted}>
                {targetName
                  ? t("summary.liveState", { target: targetName, time: formatRelativeTime(result.live.checked_at) })
                  : t("summary.liveStateUnnamed", { time: formatRelativeTime(result.live.checked_at) })}
                {result.live.oldest_transaction_seconds != null &&
                  ` · ${t("summary.oldestTransaction", { seconds: result.live.oldest_transaction_seconds })}`}
              </span>
            )}
          </div>

          {result.findings.length > 0 && (
            <section className={styles.findings} aria-label={t("findingsLabel")}>
              {result.findings.map((finding) => (
                <FindingCard key={finding.id} finding={finding} />
              ))}
            </section>
          )}

          <Card title={t("statementsTitle")} flush>
            <StatementsTable statements={result.statements} live={Boolean(result.live)} />
          </Card>
        </>
      )}

      {!result && !lint.isPending && !lint.error && (
        <Card>
          <EmptyState icon={FileCode} title={t("empty.title")}>
            {t("empty.body")}
          </EmptyState>
        </Card>
      )}
    </div>
  );
}
