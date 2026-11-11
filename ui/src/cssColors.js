import { useSyncExternalStore } from "react";

// Canvas-drawn charts (the Workload Map's cytoscape graph) can't use
// var(--token), and cytoscape parses colours itself — hex/rgb/hsl only, no
// oklch. These helpers resolve a design token to an rgb() string by letting
// the browser paint it onto a 1x1 canvas and reading the pixel back.

let context = null;

export function resolveColor(cssColor) {
  if (!context) {
    const canvas = document.createElement("canvas");
    canvas.width = 1;
    canvas.height = 1;
    context = canvas.getContext("2d", { willReadFrequently: true });
  }
  context.clearRect(0, 0, 1, 1);
  context.fillStyle = "#000";
  context.fillStyle = cssColor;
  context.fillRect(0, 0, 1, 1);
  const [r, g, b, a] = context.getImageData(0, 0, 1, 1).data;
  return a === 255 ? `rgb(${r}, ${g}, ${b})` : `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(3)})`;
}

// { name: rgb() } for each `--name` token, as currently themed.
export function readColorTokens(names) {
  const style = getComputedStyle(document.documentElement);
  return Object.fromEntries(names.map((name) => [name, resolveColor(style.getPropertyValue(`--${name}`).trim())]));
}

export function readToken(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();
}

function parseRgb(rgb) {
  return rgb.match(/[\d.]+/g).slice(0, 3).map(Number);
}

// Linear blend of two rgb() strings, t in [0, 1].
export function mixRgb(from, to, t) {
  const a = parseRgb(from);
  const b = parseRgb(to);
  const [r, g, bl] = a.map((v, i) => Math.round(v + (b[i] - v) * t));
  return `rgb(${r}, ${g}, ${bl})`;
}

// Changes whenever the effective theme does — a pinned data-theme on <html>
// or the OS preference — so canvas colours can be re-resolved. Watching the
// DOM rather than ThemeProvider's state matters: ThemeProvider writes
// data-theme in its own effect, which runs after its children's effects.
const darkQuery = typeof window !== "undefined" ? window.matchMedia("(prefers-color-scheme: dark)") : null;

function subscribe(callback) {
  const observer = new MutationObserver(callback);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  darkQuery?.addEventListener("change", callback);
  return () => {
    observer.disconnect();
    darkQuery?.removeEventListener("change", callback);
  };
}

function getSnapshot() {
  return `${document.documentElement.getAttribute("data-theme") ?? "system"}|${darkQuery?.matches ? "dark" : "light"}`;
}

export function useThemeSnapshot() {
  return useSyncExternalStore(subscribe, getSnapshot);
}
