import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Sparkles } from "lucide-react";
import { formatRelativeTime } from "./format";
import { queryAnalysesQuery } from "./queries";
import { Alert, Badge, Card, EmptyState, Skeleton } from "./components";
import tableStyles from "./components/Table.module.css";
import styles from "./AiAnalysisScreen.module.css";

const POLL_INTERVAL_MS = 5000;

// labelKey: translation key in the aiAnalysis namespace, resolved at render.
const STATUS_META = {
  pending: { labelKey: "status.pending", tone: "attention" },
  done: { labelKey: "status.done", tone: "healthy" },
  error: { labelKey: "status.error", tone: "critical" },
};

export default function AiAnalysisScreen({ targetId }) {
  const { t } = useTranslation("aiAnalysis");
  // Same cache entry the app-wide "analysis ready" watcher (App.jsx) polls;
  // this screen just polls it faster while it's open.
  const { data, error, isPending } = useQuery({
    ...queryAnalysesQuery(targetId),
    refetchInterval: POLL_INTERVAL_MS,
  });
  const analyses = data?.analyses;

  return (
    <Card title={t("history.title")} description={t("history.description")}>
      {error && <Alert tone="critical">{error.message}</Alert>}

      {isPending && <Skeleton lines={4} label={t("history.loading")} />}

      {analyses && analyses.length === 0 && (
        <EmptyState icon={Sparkles} title={t("history.emptyTitle")}>
          {t("history.emptyText")}
        </EmptyState>
      )}

      {analyses && analyses.length > 0 && (
        <ul className={styles.list}>
          {analyses.map((a) => (
            <AnalysisRow key={a.id} analysis={a} />
          ))}
        </ul>
      )}
    </Card>
  );
}

function AnalysisRow({ analysis }) {
  const { t } = useTranslation("aiAnalysis");
  const [expanded, setExpanded] = useState(false);
  const detailsId = useId();
  const status = STATUS_META[analysis.status] ?? STATUS_META.pending;

  return (
    <li className={styles.row}>
      <h3 className={styles.rowHeading}>
        <button
          type="button"
          className={styles.rowButton}
          aria-expanded={expanded}
          aria-controls={expanded ? detailsId : undefined}
          onClick={() => setExpanded((prev) => !prev)}
        >
          <ChevronRight size={14} className={tableStyles.chevron} aria-hidden="true" />
          <span title={analysis.original_query} className={styles.rowQuery}>
            {analysis.original_query}
          </span>
          <span className={styles.rowTime}>{formatRelativeTime(analysis.created_at)}</span>
          <Badge tone={status.tone} className={styles.status}>
            {t(status.labelKey)}
          </Badge>
        </button>
      </h3>

      {expanded && (
        <div id={detailsId} className={styles.details}>
          <Detail label={analysis.anonymize ? t("history.querySentAnonymized") : t("history.querySent")}>
            {analysis.anonymized_query ?? analysis.original_query}
          </Detail>

          {analysis.explain_plan && <Detail label="EXPLAIN (ANALYZE, BUFFERS)">{analysis.explain_plan}</Detail>}

          {analysis.status === "done" && analysis.ai_response && (
            <div className={styles.detail}>
              <h4 className={styles.detailLabel}>
                {t("history.aiRecommendation")}
                {analysis.model_name ? ` — ${analysis.model_name}` : ""}
              </h4>
              <div className={styles.responseBox}>
                <AiResponseView text={analysis.ai_response} />
              </div>
            </div>
          )}

          {analysis.status === "error" && analysis.error && (
            <Alert tone="critical" compact>
              {analysis.error}
            </Alert>
          )}

          {analysis.status === "pending" && (
            <p className={styles.note}>{t("history.stillRunning")}</p>
          )}
        </div>
      )}
    </li>
  );
}

function Detail({ label, children }) {
  return (
    <div className={styles.detail}>
      <h4 className={styles.detailLabel}>{label}</h4>
      <pre className={styles.detailText}>{children}</pre>
    </div>
  );
}

// Minimal markdown-lite rendering for the AI's response — no dependency,
// just enough structure (## headings, ```sql fences, bullet lines, **bold**)
// to make the "What's wrong" / "Suggested rewrites" shape the prompt asks
// for (routers/query_analysis.py::PROMPT_TEMPLATE) actually readable, since
// a raw pre-wrap block would otherwise show literal ``` and ## characters.
function splitCodeBlocks(text) {
  const blocks = [];
  const fence = /```(\w*)\n([\s\S]*?)```/g;
  let lastIndex = 0;
  let match;
  while ((match = fence.exec(text)) !== null) {
    if (match.index > lastIndex) blocks.push({ type: "text", value: text.slice(lastIndex, match.index) });
    blocks.push({ type: "code", value: match[2].trim() });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) blocks.push({ type: "text", value: text.slice(lastIndex) });
  return blocks;
}

function renderInlineBold(line, key) {
  const parts = line.split(/(\*\*[^*]+\*\*)/g);
  return (
    <div key={key}>
      {parts.map((part, i) =>
        part.startsWith("**") && part.endsWith("**") ? <strong key={i}>{part.slice(2, -2)}</strong> : part
      )}
    </div>
  );
}

function AiResponseView({ text }) {
  return (
    <div className={styles.response}>
      {splitCodeBlocks(text).map((block, i) =>
        block.type === "code" ? (
          <pre key={i} className={styles.responseCode}>
            {block.value}
          </pre>
        ) : (
          block.value.split("\n").map((line, j) => {
            const key = `${i}-${j}`;
            const trimmed = line.trim();
            if (trimmed.startsWith("## ")) {
              return (
                <div
                  key={key}
                  className={[styles.responseHeading, j === 0 && styles.responseHeadingFirst].filter(Boolean).join(" ")}
                >
                  {trimmed.slice(3)}
                </div>
              );
            }
            if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
              return (
                <div key={key} className={styles.bullet}>
                  <span aria-hidden="true">•</span>
                  {renderInlineBold(trimmed.slice(2), `${key}-b`)}
                </div>
              );
            }
            if (trimmed === "") return <div key={key} className={styles.spacer} />;
            return renderInlineBold(line, key);
          })
        )
      )}
    </div>
  );
}
