import { useId, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import QueryParamsForm from "./QueryParamsForm";
import { applyPlaceholderValues, extractPlaceholders, splitOnPlaceholders } from "./queryParams";
import { targetKey } from "./queryClient";
import { queryAnalysesQuery } from "./queries";
import { Alert, Checkbox, Dialog } from "./components";
import styles from "./QueryAnalysisModal.module.css";

// The parent only renders this while a query is picked, so the dialog is
// always open; closing goes through onClose (Esc, backdrop, the close
// button, Cancel). Dialog returns focus to the row's button afterwards.
export default function QueryAnalysisModal({ targetId, query, onClose, onCreated }) {
  const queryClient = useQueryClient();
  const formId = useId();
  const [anonymize, setAnonymize] = useState(true);
  const [paramValues, setParamValues] = useState({});

  const placeholders = useMemo(() => extractPlaceholders(query), [query]);
  const allParamsFilled = placeholders.every((n) => (paramValues[n] ?? "").trim() !== "");
  const finalQuery = useMemo(
    () => (placeholders.length > 0 ? applyPlaceholderValues(query, paramValues) : query),
    [query, paramValues, placeholders.length]
  );

  // What the anonymized query will look like. Keyed on the exact query text
  // and the flag, and only fetched while anonymizing is on and every
  // placeholder has a value — a half-filled template isn't worth a request.
  const preview = useQuery({
    queryKey: targetKey(targetId, "anonymize-preview", finalQuery, anonymize),
    queryFn: ({ signal }) => api.anonymizeQueryPreview(targetId, finalQuery, { signal }),
    enabled: anonymize && allParamsFilled,
    // Same input, same output.
    staleTime: Infinity,
  });
  const previewing = anonymize && allParamsFilled && preview.isPending;

  const createAnalysis = useMutation({
    mutationFn: () => api.createQueryAnalysis(targetId, { query: finalQuery, anonymize }),
    onSuccess: (analysis) => {
      onCreated?.(analysis);
      // AI Analysis and the app-wide "analysis ready" watcher read this
      // cache; refreshing it now picks up the new pending row right away.
      queryClient.invalidateQueries({ queryKey: queryAnalysesQuery(targetId).queryKey });
    },
  });
  const submitted = createAnalysis.isSuccess;
  const canRun = !createAnalysis.isPending && !previewing && allParamsFilled;

  const handleSubmit = (event) => {
    event.preventDefault();
    if (canRun) createAnalysis.mutate();
  };

  const displayedQuery = anonymize && preview.data ? preview.data.anonymized_query : finalQuery;
  const error = createAnalysis.error ?? (anonymize ? preview.error : null);

  const footer = submitted ? (
    <button type="button" className="button-primary" onClick={onClose}>
      Close
    </button>
  ) : (
    <>
      <button type="button" className="button-secondary" onClick={onClose}>
        Cancel
      </button>
      <button type="submit" form={formId} className="button-primary" disabled={!canRun}>
        {createAnalysis.isPending ? "Starting…" : "Run Analysis"}
      </button>
    </>
  );

  return (
    <Dialog open onClose={onClose} title="Analyze with AI" size="lg" footer={footer}>
      {!submitted && (
        <form id={formId} className={styles.form} onSubmit={handleSubmit}>
          <p className={styles.lead}>
            {placeholders.length > 0 && !allParamsFilled
              ? "Query template — fill in a value for each $N placeholder below to see the query that will actually run."
              : anonymize
                ? "Table and column names below are anonymized before anything is sent to the AI provider."
                : "This is the exact query text that will be sent to the AI provider, unmodified."}
          </p>

          <pre className={styles.preview}>
            {placeholders.length > 0 && !allParamsFilled
              ? splitOnPlaceholders(query).map((part, i) =>
                  part.type === "placeholder" ? (
                    <mark key={i} className={styles.placeholderToken}>
                      {part.value}
                    </mark>
                  ) : (
                    <span key={i}>{part.value}</span>
                  )
                )
              : previewing
                ? "Anonymizing…"
                : displayedQuery}
          </pre>

          <QueryParamsForm
            placeholders={placeholders}
            values={paramValues}
            onChange={(n, value) => setParamValues((prev) => ({ ...prev, [n]: value }))}
          />

          <Checkbox checked={anonymize} onChange={(e) => setAnonymize(e.target.checked)}>
            Anonymize table/column names
          </Checkbox>

          {error && <Alert tone="critical">{error.message}</Alert>}

          <p className={styles.footnote}>
            Runs EXPLAIN (ANALYZE, BUFFERS) against your database, then sends the query and plan to the AI
            provider configured in Settings. Only literal values are already redacted (pg_stat_statements
            normalizes those to $1/$2/... before this app ever sees the query) — anonymizing here additionally
            hides real table/column names.
          </p>
        </form>
      )}

      {submitted && (
        <Alert tone="healthy">
          Analysis started. It's running in the background — you'll see a notification on the AI Analysis tab
          when it's ready, or check there any time.
        </Alert>
      )}
    </Dialog>
  );
}
