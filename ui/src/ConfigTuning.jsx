import { useEffect, useState } from "react";
import { api } from "./api";
import HeaderHint from "./HeaderHint";

const COLUMNS = [
  { label: "Parameter", help: "The postgresql.conf setting name." },
  { label: "Current", help: "The value currently active on your target database." },
  {
    label: "Recommended",
    help: "DbButler's suggested value, sized from your saved Hardware Profile (RAM/CPU/storage/workload).",
  },
  { label: "Why", help: "The reasoning behind the recommendation." },
  {
    label: "",
    help: "A badge here means this change only takes effect after a manual PostgreSQL restart — everything else applies via a reload (pg_reload_conf()).",
  },
];

export default function ConfigTuning({ targetId }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(null);

  useEffect(() => {
    api
      .getConfigTuning(targetId)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((err) => setError(err.message));
  }, [targetId]);

  const copy = (kind, text) => {
    navigator.clipboard
      .writeText(text)
      .then(() => {
        setCopied(kind);
        setTimeout(() => setCopied(null), 1500);
      })
      .catch(() => {});
  };

  const alterSystemScript = (rows) =>
    rows.map((r) => `ALTER SYSTEM SET ${r.name} = '${r.recommended_value}';`).join("\n") +
    "\n\n-- Reload-only settings take effect immediately after:\nSELECT pg_reload_conf();\n" +
    "-- Settings marked \"restart\" need a manual PostgreSQL restart to apply.";

  const confSnippet = (rows) => rows.map((r) => `${r.name} = ${r.recommended_value}`).join("\n");

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 14,
        padding: "18px 20px",
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: 14,
      }}
    >
      <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)" }}>Config Tuning</span>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "12px 14px",
          background: "var(--accent-tint)",
          borderRadius: 10,
        }}
      >
        <span style={{ fontSize: 12, color: "oklch(35% 0.1 255)", lineHeight: 1.5 }}>
          <b>Recommend only.</b> Nothing here changes your database automatically — copy or export
          these values and apply them yourself when you're ready.
        </span>
      </div>

      {error && (
        <div
          style={{
            padding: "10px 12px",
            background: error.includes("Hardware Profile") ? "var(--accent-tint)" : "var(--critical-bg)",
            border: error.includes("Hardware Profile") ? "none" : "1px solid var(--critical-border)",
            borderRadius: 9,
            color: error.includes("Hardware Profile") ? "oklch(35% 0.1 255)" : "var(--critical-text)",
            fontSize: 12.5,
          }}
        >
          {error}
        </div>
      )}

      {data && (
        <>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr>
                  {COLUMNS.map((col) => (
                    <th
                      key={col.label || "restart"}
                      style={{
                        textAlign: "left",
                        padding: "6px 10px",
                        fontSize: 10.5,
                        fontWeight: 700,
                        textTransform: "uppercase",
                        letterSpacing: "0.02em",
                        color: "var(--text-muted)",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {col.label}
                      <HeaderHint text={col.help} />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => (
                  <tr key={row.name}>
                    <td
                      style={{
                        padding: "8px 10px",
                        fontFamily: "IBM Plex Mono, monospace",
                        fontWeight: 600,
                        color: "var(--text)",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {row.name}
                    </td>
                    <td
                      style={{
                        padding: "8px 10px",
                        fontFamily: "IBM Plex Mono, monospace",
                        color: "var(--text-muted)",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {row.current_value}
                    </td>
                    <td
                      style={{
                        padding: "8px 10px",
                        fontFamily: "IBM Plex Mono, monospace",
                        fontWeight: 650,
                        color: "var(--healthy-text)",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {row.recommended_value}
                    </td>
                    <td style={{ padding: "8px 10px", color: "var(--text-secondary)", lineHeight: 1.4 }}>
                      {row.why}
                    </td>
                    <td style={{ padding: "8px 10px" }}>
                      {row.requires_restart && (
                        <span
                          style={{
                            fontSize: 10,
                            fontWeight: 700,
                            letterSpacing: "0.02em",
                            textTransform: "uppercase",
                            color: "var(--attention-text)",
                            background: "var(--attention-bg)",
                            borderRadius: 6,
                            padding: "3px 7px",
                            whiteSpace: "nowrap",
                          }}
                        >
                          Restart
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <button className="button-primary" onClick={() => copy("alter", alterSystemScript(data.rows))}>
              {copied === "alter" ? "Copied" : "Copy as ALTER SYSTEM script"}
            </button>
            <button className="button-secondary" onClick={() => copy("conf", confSnippet(data.rows))}>
              {copied === "conf" ? "Copied" : "Copy as postgresql.conf snippet"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
