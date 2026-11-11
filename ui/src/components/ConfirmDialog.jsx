import { createContext, useCallback, useContext, useRef, useState } from "react";
import Dialog from "./Dialog";

// One confirmation flow for every destructive or disruptive action
// (terminate a backend, VACUUM, remove a connection, …), replacing the mix
// of window.confirm, inline panels and no confirmation at all.
//
//   const confirm = useConfirm();
//   if (!(await confirm({ title: "Remove connection?", message: …,
//     confirmLabel: "Remove", tone: "danger" }))) return;
//
// Resolves true on confirm, false on cancel/Esc/backdrop. For a danger
// action the Cancel button gets initial focus, so a stray Enter is safe.
const ConfirmContext = createContext(null);

export function ConfirmProvider({ children }) {
  const [request, setRequest] = useState(null);

  const confirm = useCallback(
    (options) =>
      new Promise((resolve) => {
        setRequest({ options, resolve });
      }),
    []
  );

  const settle = (value) => {
    request?.resolve(value);
    setRequest(null);
  };

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <ConfirmDialog
        open={Boolean(request)}
        {...(request?.options ?? {})}
        onConfirm={() => settle(true)}
        onCancel={() => settle(false)}
      />
    </ConfirmContext.Provider>
  );
}

export function useConfirm() {
  const confirm = useContext(ConfirmContext);
  if (!confirm) throw new Error("useConfirm must be used inside <ConfirmProvider>");
  return confirm;
}

function ConfirmDialog({
  open,
  title = "Are you sure?",
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "default",
  onConfirm,
  onCancel,
}) {
  const cancelRef = useRef(null);
  const confirmRef = useRef(null);
  const danger = tone === "danger";

  return (
    <Dialog
      open={open}
      onClose={onCancel}
      title={title}
      size="sm"
      initialFocusRef={danger ? cancelRef : confirmRef}
      footer={
        <>
          <button ref={cancelRef} type="button" className="button-secondary" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            type="button"
            className={danger ? "button-danger" : "button-primary"}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </>
      }
    >
      {message}
    </Dialog>
  );
}
