import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Copy } from "lucide-react";
import { copyText } from "../clipboard";

const RESET_MS = 2000;

// Copy-to-clipboard button that works on plain-http installs too (see
// clipboard.js). Shows "Copied" / "Copy failed" for two seconds, announced
// through a polite live region.
export default function CopyButton({ text, label, className = "button-secondary button-sm" }) {
  const { t } = useTranslation();
  const [state, setState] = useState("idle"); // idle | copied | failed

  useEffect(() => {
    if (state === "idle") return undefined;
    const timeout = setTimeout(() => setState("idle"), RESET_MS);
    return () => clearTimeout(timeout);
  }, [state]);

  const handleClick = async () => {
    setState((await copyText(text)) ? "copied" : "failed");
  };

  const shownLabel =
    state === "copied" ? t("actions.copied") : state === "failed" ? t("actions.copyFailed") : (label ?? t("actions.copy"));

  return (
    <>
      <button
        type="button"
        className={className}
        onClick={handleClick}
        title={state === "failed" ? t("actions.copyBlocked") : undefined}
      >
        {state === "copied" ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
        {shownLabel}
      </button>
      <span className="sr-only" role="status">
        {state === "copied"
          ? t("actions.copiedAnnouncement")
          : state === "failed"
            ? t("actions.copyFailedAnnouncement")
            : ""}
      </span>
    </>
  );
}
