import { useEffect, useState } from "react";
import { api, getAuthToken, onUnauthorized, setAuthToken } from "./api";

// Gates the whole app behind the shared AUTH_TOKEN the backend was set up
// with (see backend/app/auth.py) — a single setup-time secret, not per-user
// login. Held in sessionStorage: re-prompts on a new tab/window, survives a
// refresh within the same one.
export default function AuthGate({ children }) {
  const [status, setStatus] = useState("checking"); // checking | needed | ok
  const [input, setInput] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const verify = (token) => {
    setAuthToken(token);
    return api
      .checkAuth()
      .then(() => {
        setStatus("ok");
        setError(null);
      })
      .catch((err) => {
        setStatus("needed");
        setError(err.message);
      });
  };

  useEffect(() => {
    onUnauthorized(() => setStatus("needed"));
    const stored = getAuthToken();
    if (stored) verify(stored);
    else setStatus("needed");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!input.trim()) return;
    setSubmitting(true);
    verify(input.trim()).finally(() => setSubmitting(false));
  };

  if (status === "checking") {
    return (
      <div style={{ display: "flex", height: "100vh", alignItems: "center", justifyContent: "center" }}>
        <span style={{ color: "var(--text-secondary)" }}>Loading&hellip;</span>
      </div>
    );
  }

  if (status === "ok") return children;

  return (
    <div style={{ display: "flex", height: "100vh", alignItems: "center", justifyContent: "center" }}>
      <form
        onSubmit={handleSubmit}
        style={{
          width: 360,
          display: "flex",
          flexDirection: "column",
          gap: 14,
          padding: 28,
          background: "var(--card)",
          border: "1px solid var(--border)",
          borderRadius: 14,
        }}
      >
        <div>
          <div style={{ fontSize: 15, fontWeight: 650, marginBottom: 4 }}>PostgreDba</div>
          <div style={{ fontSize: 12.5, color: "var(--text-secondary)" }}>
            Enter the access token this instance was set up with — the{" "}
            <code>APP_AUTH_TOKEN</code> value in its <code>.env</code> file.
          </div>
        </div>
        <input
          type="password"
          autoFocus
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Access token"
        />
        {error && <div style={{ fontSize: 12.5, color: "var(--critical-text)" }}>{error}</div>}
        <button type="submit" className="button-primary" disabled={submitting || !input.trim()}>
          {submitting ? "Checking…" : "Continue"}
        </button>
      </form>
    </div>
  );
}
