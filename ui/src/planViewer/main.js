// The plan viewer page (plan-viewer.html): Dalibo's pev2 on its own, inside
// an iframe opened by components/PlanViewerFrame.jsx. pev2 is a Vue
// component that needs Bootstrap's global CSS — isolating it here keeps both
// out of the React app, and the page is only fetched when a plan is opened.
//
// Protocol (postMessage, same origin only, from the parent window only):
//   page   -> parent  { type: "dbbutler:plan-viewer-ready" }
//   parent -> page    { type: "dbbutler:plan", plan, query, theme }
// `plan` is a string: EXPLAIN text output or EXPLAIN (FORMAT JSON) text.
import { createApp, h, reactive } from "vue";
import { Plan } from "pev2";
import "bootstrap/dist/css/bootstrap.min.css";
import "pev2/dist/pev2.css";
import "./planViewer.css";

const state = reactive({ plan: null, query: "", version: 0 });

createApp({
  render: () =>
    state.plan
      ? h(Plan, { key: state.version, planSource: state.plan, planQuery: state.query })
      : h("p", { class: "p-3 text-body-secondary" }, "Waiting for a plan…"),
}).mount("#app");

window.addEventListener("message", (event) => {
  if (event.origin !== window.location.origin || event.source !== window.parent) return;
  const { type, plan, query, theme } = event.data ?? {};
  if (type !== "dbbutler:plan" || typeof plan !== "string") return;
  // Bootstrap 5.3 colour modes follow data-bs-theme.
  document.documentElement.setAttribute("data-bs-theme", theme === "dark" ? "dark" : "light");
  state.plan = plan;
  state.query = typeof query === "string" ? query : "";
  state.version += 1;
});

window.parent.postMessage({ type: "dbbutler:plan-viewer-ready" }, window.location.origin);
