import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import { copyText } from "../clipboard";

const RESET_MS = 2000;

// Copy-to-clipboard button that works on plain-http installs too (see
// clipboard.js). Shows "Copied" / "Copy failed" for two seconds, announced
// through a polite live region.
export default function CopyButton({ text, label = "Copy", className = "button-secondary button-sm" }) {
  const [state, setState] = useState("idle"); // idle | copied | failed

  useEffect(() => {
    if (state === "idle") return undefined;
    const timeout = setTimeout(() => setState("idle"), RESET_MS);
    return () => clearTimeout(timeout);
  }, [state]);

  const handleClick = async () => {
    setState((await copyText(text)) ? "copied" : "failed");
  };

  const shownLabel = state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : label;

  return (
    <>
      <button
        type="button"
        className={className}
        onClick={handleClick}
        title={state === "failed" ? "Your browser blocked clipboard access — select the text and copy it manually." : undefined}
      >
        {state === "copied" ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
        {shownLabel}
      </button>
      <span className="sr-only" role="status">
        {state === "copied" ? "Copied to clipboard" : state === "failed" ? "Copy failed, select the text manually" : ""}
      </span>
    </>
  );
}
