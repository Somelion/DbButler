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
  server: {
    host: true,
    port: 5173,
    allowedHosts: allowedHosts.length > 0 ? allowedHosts : true,
  },
});
