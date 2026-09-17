import { useEffect, useState } from "react";
import { api } from "./api";
import { formatRelativeTime } from "./format";

const POLL_INTERVAL_MS = 5000;

const STATUS_META = {
  pending: { label: "Running…", bg: "var(--attention-bg)", text: "var(--attention-text)" },
  done: { label: "Done", bg: "var(--healthy-bg)", text: "var(--healthy-text)" },
  error: { label: "Error", bg: "var(--critical-bg)", text: "var(--critical-text)" },
};

export default function AiAnalysisScreen({ targetId }) {
  const [analyses, setAnalyses] = useState(null);
  const [error, setError] = useState(null);

  const refresh = () =>
    api
      .getQueryAnalyses(targetId)
      .then((data) => {
        setAnalyses(data.analyses);
        setError(null);
      })
      .catch((err) => setError(err.message));

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetId]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 10,
        padding: "18px 20px",
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: 14,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span style={{ fontFamily: "Manrope, sans-serif", fontWeight: 650, fontSize: 14, color: "var(--text)" }}>
          AI Analysis history
        </span>
        <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
          Every query you've sent for AI analysis from Query Intelligence, newest first. Click a row for the full
          EXPLAIN plan and the AI's response.
        </span>
      </div>

      {error && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{error}</span>}

      {analyses && analyses.length === 0 && (
        <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
          Nothing here yet — use "Analyze with AI" on a query in Query Intelligence to start one.
        </span>
      )}

      {analyses && analyses.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {analyses.map((a) => (
            <AnalysisRow key={a.id} analysis={a} />
          ))}
        </div>
      )}
    </div>
  );
}

function AnalysisRow({ analysis }) {
  const [expanded, setExpanded] = useState(false);
  const status = STATUS_META[analysis.status] ?? STATUS_META.pending;

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 12, overflow: "hidden" }}>
      <div
        onClick={() => setExpanded((prev) => !prev)}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          padding: "10px 14px",
          background: "var(--row-bg)",
          cursor: "pointer",
        }}
      >
        <span
          title={analysis.original_query}
          style={{
            fontFamily: "IBM Plex Mono, monospace",
            fontSize: 12,
            color: "var(--text)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            flex: "1 1 auto",
            // See QueryPicker.jsx's identical comment: minWidth:0 alone lets
            // this column shrink to nothing on a narrow enough row once its
            // non-shrinking siblings (timestamp, status badge) claim the
            // rest — a real pixel floor keeps it legible instead.
            minWidth: 160,
          }}
        >
          {analysis.original_query}
        </span>
        <span style={{ fontSize: 11, color: "var(--text-muted)", whiteSpace: "nowrap" }}>
          {formatRelativeTime(analysis.created_at)}
        </span>
        <span
          style={{
            padding: "3px 9px",
            borderRadius: 100,
            background: status.bg,
            color: status.text,
            fontSize: 11,
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.03em",
            whiteSpace: "nowrap",
          }}
        >
          {status.label}
        </span>
      </div>

      {expanded && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: "12px 14px" }}>
          <Detail label={analysis.anonymize ? "Query sent (anonymized)" : "Query sent"}>
            {analysis.anonymized_query ?? analysis.original_query}
          </Detail>

          {analysis.explain_plan && <Detail label="EXPLAIN (ANALYZE, BUFFERS)">{analysis.explain_plan}</Detail>}

          {analysis.status === "done" && analysis.ai_response && (
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span
                style={{
                  fontSize: 10.5,
                  color: "var(--text-muted)",
                  textTransform: "uppercase",
                  letterSpacing: "0.03em",
                  fontWeight: 700,
                }}
              >
                AI recommendation{analysis.model_name ? ` — ${analysis.model_name}` : ""}
              </span>
              <div
                style={{
                  padding: "10px 12px",
                  background: "var(--bg)",
                  border: "1px solid var(--border)",
                  borderRadius: 8,
                  maxHeight: 420,
                  overflow: "auto",
                }}
              >
                <AiResponseView text={analysis.ai_response} />
              </div>
            </div>
          )}

          {analysis.status === "error" && analysis.error && (
            <span style={{ fontSize: 12, color: "var(--critical-text)" }}>{analysis.error}</span>
          )}

          {analysis.status === "pending" && (
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              Still running — this row updates automatically once the AI responds.
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function Detail({ label, children }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span
        style={{
          fontSize: 10.5,
          color: "var(--text-muted)",
          textTransform: "uppercase",
          letterSpacing: "0.03em",
          fontWeight: 700,
        }}
      >
        {label}
      </span>
      <pre
        style={{
          margin: 0,
          padding: "10px 12px",
          background: "var(--bg)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          fontFamily: "IBM Plex Mono, monospace",
          fontSize: 12,
          color: "var(--text)",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          maxHeight: 320,
          overflow: "auto",
        }}
      >
        {children}
      </pre>
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
    <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12.5, color: "var(--text)", lineHeight: 1.5 }}>
      {splitCodeBlocks(text).map((block, i) =>
        block.type === "code" ? (
          <pre
            key={i}
            style={{
              margin: 0,
              padding: "10px 12px",
              background: "var(--row-bg)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              fontFamily: "IBM Plex Mono, monospace",
              fontSize: 12,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              overflowX: "auto",
            }}
          >
            {block.value}
          </pre>
        ) : (
          block.value.split("\n").map((line, j) => {
            const key = `${i}-${j}`;
            const trimmed = line.trim();
            if (trimmed.startsWith("## ")) {
              return (
                <div key={key} style={{ fontWeight: 700, fontSize: 13, marginTop: j === 0 ? 0 : 6 }}>
                  {trimmed.slice(3)}
                </div>
              );
            }
            if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
              return (
                <div key={key} style={{ display: "flex", gap: 6, paddingLeft: 4 }}>
                  <span>•</span>
                  {renderInlineBold(trimmed.slice(2), `${key}-b`)}
                </div>
              );
            }
            if (trimmed === "") return <div key={key} style={{ height: 4 }} />;
            return renderInlineBold(line, key);
          })
        )
      )}
    </div>
  );
}
