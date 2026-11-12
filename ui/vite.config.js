import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite's dev server rejects requests whose Host header isn't localhost/an IP
// (anti DNS-rebinding). This app is meant to be self-hosted behind whatever
// hostname/reverse proxy each deployer uses, and it already gates every
// request on APP_AUTH_TOKEN, so the Host check is disabled by default —
// nobody should have to know about UI_ALLOWED_HOSTS just to get the UI to
// load. Set UI_ALLOWED_HOSTS (comma-separated) to lock it back down to a
// specific list of hostnames instead.
const allowedHosts = (process.env.UI_ALLOWED_HOSTS ?? "")
  .split(",")
  .map((host) => host.trim())
  .filter(Boolean);

export default defineConfig({
  plugins: [react()],
  build: {
    // Two lazy chunks are big on purpose, and load only when used: the
    // Workload Map (cytoscape + fcose, ~590 kB) and its 3D view (three.js,
    // ~1.4 MB / ~390 kB gzipped). The main bundle stays near 500 kB.
    chunkSizeWarningLimit: 1500,
    rolldownOptions: {
      // Two pages: the app, and the pev2 plan viewer it shows in an iframe
      // (src/planViewer/main.js) — kept apart so Vue and Bootstrap's global
      // CSS never load into the app itself.
      input: {
        main: fileURLToPath(new URL("./index.html", import.meta.url)),
        planViewer: fileURLToPath(new URL("./plan-viewer.html", import.meta.url)),
      },
    },
  },
  server: {
    host: true,
    port: 5173,
    allowedHosts: allowedHosts.length > 0 ? allowedHosts : true,
  },
});
