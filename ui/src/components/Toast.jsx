import { createContext, useCallback, useContext, useRef, useState } from "react";
import { X } from "lucide-react";
import styles from "./Toast.module.css";

// App-wide toasts. The live region is always mounted, so screen readers
// announce a toast the moment it is added.
//
//   const toast = useToast();
//   toast("An AI query analysis is ready.");
const ToastContext = createContext(null);
const DEFAULT_DURATION_MS = 6000;

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const nextId = useRef(0);

  const dismiss = useCallback((id) => setToasts((list) => list.filter((t) => t.id !== id)), []);

  const show = useCallback(
    (message, { duration = DEFAULT_DURATION_MS } = {}) => {
      nextId.current += 1;
      const id = nextId.current;
      setToasts((list) => [...list, { id, message }]);
      if (duration > 0) setTimeout(() => dismiss(id), duration);
      return id;
    },
    [dismiss]
  );

  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className={styles.region} role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={styles.toast}>
            <span className={styles.message}>{t.message}</span>
            <button type="button" className={styles.dismiss} onClick={() => dismiss(t.id)} aria-label="Dismiss">
              <X size={14} aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const show = useContext(ToastContext);
  if (!show) throw new Error("useToast must be used inside <ToastProvider>");
  return show;
}
