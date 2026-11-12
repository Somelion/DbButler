import { useEffect, useRef } from "react";
import { useThemeSnapshot } from "../cssColors";
import styles from "./PlanViewer.module.css";

const VIEWER_URL = `${import.meta.env.BASE_URL}plan-viewer.html`;

function effectiveTheme() {
  const pinned = document.documentElement.getAttribute("data-theme");
  if (pinned === "light" || pinned === "dark") return pinned;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

// pev2 (Dalibo's plan visualiser) in an iframe — see src/planViewer/main.js
// for why it lives on its own page. `plan` is EXPLAIN output as a string
// (text format, or FORMAT JSON serialized). The plan is (re)sent whenever
// the page says it's ready and whenever plan, query or theme change.
export default function PlanViewerFrame({ plan, query, title = "Query plan" }) {
  const frameRef = useRef(null);
  const latestRef = useRef({ plan, query });
  const themeSnapshot = useThemeSnapshot();

  useEffect(() => {
    latestRef.current = { plan, query };
  });

  useEffect(() => {
    const post = () => {
      const { plan: currentPlan, query: currentQuery } = latestRef.current;
      frameRef.current?.contentWindow?.postMessage(
        { type: "dbbutler:plan", plan: currentPlan, query: currentQuery ?? "", theme: effectiveTheme() },
        window.location.origin
      );
    };
    const onMessage = (event) => {
      if (event.origin !== window.location.origin || event.source !== frameRef.current?.contentWindow) return;
      if (event.data?.type === "dbbutler:plan-viewer-ready") post();
    };
    window.addEventListener("message", onMessage);
    // Already loaded (plan or theme changed while open): send it now too.
    post();
    return () => window.removeEventListener("message", onMessage);
  }, [plan, query, themeSnapshot]);

  return <iframe ref={frameRef} src={VIEWER_URL} title={title} className={styles.frame} />;
}
