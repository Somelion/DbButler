import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Trans, useTranslation } from "react-i18next";
import styles from "./AuthGate.module.css";
import { api, getAuthToken, onUnauthorized, setAuthToken } from "./api";
import { Field, Skeleton } from "./components";

// Gates the whole app behind the shared AUTH_TOKEN the backend was set up
// with (see backend/app/auth.py) — a single setup-time secret, not per-user
// login. Held in sessionStorage: re-prompts on a new tab/window, survives a
// refresh within the same one.
export default function AuthGate({ children }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState(() => (getAuthToken() ? "checking" : "needed")); // checking | needed | ok
  const [input, setInput] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef(null);

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
    // A 401 from any screen: drop every cached response (they belong to the
    // old session) and show the prompt again.
    onUnauthorized(() => {
      queryClient.clear();
      setStatus("needed");
    });
    const stored = getAuthToken();
    if (stored) verify(stored);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The prompt is the only thing on the page, so start typing right away.
  useEffect(() => {
    if (status === "needed") inputRef.current?.focus();
  }, [status]);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!input.trim()) return;
    setSubmitting(true);
    verify(input.trim()).finally(() => setSubmitting(false));
  };

  if (status === "checking") {
    return (
      <div className={styles.page}>
        <Skeleton lines={2} width="240px" label={t("auth.checkingAccess")} />
      </div>
    );
  }

  if (status === "ok") return children;

  return (
    <main className={styles.page}>
      <form onSubmit={handleSubmit} className={styles.card}>
        <div className={styles.intro}>
          <h1 className={styles.title}>DbButler</h1>
          <p className={styles.description}>
            <Trans i18nKey="auth.intro" components={{ code: <code /> }} />
          </p>
        </div>
        <Field label={t("auth.token")} error={error}>
          <input
            ref={inputRef}
            type="password"
            autoComplete="current-password"
            value={input}
            onChange={(e) => setInput(e.target.value)}
          />
        </Field>
        <button type="submit" className="button-primary" disabled={submitting || !input.trim()}>
          {submitting ? t("auth.checking") : t("auth.continue")}
        </button>
      </form>
    </main>
  );
}
