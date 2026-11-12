import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { FileCode } from "lucide-react";
import styles from "./MigrationLintScreen.module.css";
import tableStyles from "./components/Table.module.css";
import { api } from "./api";
import FindingCard from "./FindingCard";
import { formatRelativeTime } from "./format";
import { Alert, Badge, Card, Checkbox, EmptyState, Field, SeverityBadge } from "./components";

const DRAFT_KEY = "dbbutler.migrationLint.draft";

const EXAMPLE = `-- Example: three common ways a migration blocks production traffic.
ALTER TABLE orders ADD COLUMN reference uuid DEFAULT gen_random_uuid();
CREATE INDEX orders_reference_idx ON orders (reference);
ALTER TABLE orders ADD CONSTRAINT orders_customer_fk
    FOREIGN KEY (customer_id) REFERENCES customers (id);
`;

// What a lock mode stops, as a badge: the text carries the meaning, the
// tone repeats it.
const BLOCKS = {
  "reads and writes": { tone: "critical", label: "blocks reads and writes" },
  writes: { tone: "attention", label: "blocks writes" },
  nothing: { tone: "healthy", label: "traffic continues" },
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
  if (locks.length === 0) return <span className={styles.muted}>—</span>;
  return (
    <ul className={styles.locks}>
      {locks.map((lock, i) => {
        const blocks = BLOCKS[lock.blocks] ?? BLOCKS.nothing;
        return (
          // A static, never-reordered list: the position is a stable key.
          <li key={i}>
            <code>{lock.mode}</code> on <code>{lock.relation}</code>
            {lock.via_index && <span className={styles.muted}> (index — locks its table)</span>}{" "}
            <Badge tone={blocks.tone}>{blocks.label}</Badge>
          </li>
        );
      })}
    </ul>
  );
}

function StatementsTable({ statements, live }) {
  return (
    <div className={tableStyles.wrap}>
      <table className={tableStyles.table}>
        <caption className="sr-only">What each statement locks and for how long</caption>
        <thead>
          <tr>
            <th scope="col" className={`${tableStyles.th} ${tableStyles.numeric}`}>Line</th>
            <th scope="col" className={tableStyles.th}>Statement</th>
            <th scope="col" className={tableStyles.th}>Locks</th>
            <th scope="col" className={tableStyles.th}>How long</th>
            {live && <th scope="col" className={tableStyles.th}>On this database now</th>}
          </tr>
        </thead>
        <tbody>
          {statements.map((stmt) => (
            <tr key={stmt.index} className={tableStyles.row}>
              <td className={`${tableStyles.td} ${tableStyles.numeric}`}>{stmt.line}</td>
              <td className={`${tableStyles.td} ${styles.statementCell}`}>
                <span className={styles.kind}>{stmt.kind}</span>
                {stmt.in_transaction && <Badge tone="neutral">in transaction</Badge>}
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
  const [sql, setSql] = useState(readDraft);
  const [useLive, setUseLive] = useState(true);
  const lint = useMutation({ mutationFn: ({ text, live }) => api.lintMigration(text, live ? targetId : null) });
  const result = lint.data;
  const targetName = target?.name ?? "this database";

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
        title="Migration"
        description="Paste the SQL of a schema migration. It is parsed with PostgreSQL's own grammar and never executed — not even in a transaction."
        actions={
          <button
            type="button"
            className="button-link"
            onClick={() => {
              setSql(EXAMPLE);
              saveDraft(EXAMPLE);
            }}
          >
            Load an example
          </button>
        }
      >
        <form className={styles.form} onSubmit={handleSubmit}>
          <Field label="SQL" hint="Several statements are fine; BEGIN/COMMIT and SET lock_timeout are taken into account.">
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
              Predict the impact on {targetName} (table sizes, sessions holding locks now, recent traffic)
            </Checkbox>
            <button type="submit" className="button-primary" disabled={!sql.trim() || lint.isPending}>
              <FileCode size={14} aria-hidden="true" />
              {lint.isPending ? "Checking…" : "Check migration"}
            </button>
          </div>
        </form>
      </Card>

      {lint.error && <Alert tone="critical">{lint.error.message}</Alert>}

      {result?.parse_error && (
        <Alert tone="critical" title={`Syntax error on line ${result.parse_error.line}, column ${result.parse_error.column}`}>
          {result.parse_error.message}
        </Alert>
      )}

      {result && !result.parse_error && (
        <>
          <div className={styles.summary} role="status">
            <SeverityBadge severity={result.overall_severity} />
            <span>
              {result.statements.length} statement{result.statements.length === 1 ? "" : "s"} ·{" "}
              {result.findings.length === 0
                ? "no risky patterns found"
                : `${counts.critical ?? 0} critical, ${counts.attention ?? 0} attention, ${counts.info ?? 0} info`}
            </span>
            {result.live && (
              <span className={styles.muted}>
                Live state of {targetName} read {formatRelativeTime(result.live.checked_at)}
                {result.live.oldest_transaction_seconds != null &&
                  ` · oldest open transaction ${result.live.oldest_transaction_seconds}s`}
              </span>
            )}
          </div>

          {result.findings.length > 0 && (
            <section className={styles.findings} aria-label="Findings">
              {result.findings.map((finding) => (
                <FindingCard key={finding.id} finding={finding} />
              ))}
            </section>
          )}

          <Card title="Statement by statement" flush>
            <StatementsTable statements={result.statements} live={Boolean(result.live)} />
          </Card>
        </>
      )}

      {!result && !lint.isPending && !lint.error && (
        <Card>
          <EmptyState icon={FileCode} title="Nothing checked yet">
            The linter flags non-concurrent index builds, table rewrites, validation under lock, missing lock_timeout
            and more — and with a database connected, says how many sessions would queue behind each lock right now.
          </EmptyState>
        </Card>
      )}
    </div>
  );
}
