import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router";
import { QueryClientProvider } from "@tanstack/react-query";

// Self-hosted fonts (no Google Fonts request, works air-gapped). Only the
// weights the tokens use: 400/500/600/700.
import "@fontsource/public-sans/400.css";
import "@fontsource/public-sans/500.css";
import "@fontsource/public-sans/600.css";
import "@fontsource/public-sans/700.css";
import "@fontsource/manrope/500.css";
import "@fontsource/manrope/600.css";
import "@fontsource/manrope/700.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "@fontsource/ibm-plex-mono/600.css";
import "./styles/tokens.css";
import "./styles/base.css";

import "./i18n";
import { useTranslation } from "react-i18next";
import App from "./App.jsx";
import AuthGate from "./AuthGate.jsx";
import { ConfirmProvider, ToastProvider } from "./components";
import { queryClient } from "./queryClient";
import { ThemeProvider } from "./theme";

// Remounts the app when the UI language changes, so every text — including
// ones computed outside components (format.js, chart labels) — switches at
// once. Fetched data stays in the query cache, which lives above this.
function LanguageRoot({ children }) {
  const { i18n } = useTranslation();
  return <React.Fragment key={i18n.language}>{children}</React.Fragment>;
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <LanguageRoot>
            <ToastProvider>
              <ConfirmProvider>
                <AuthGate>
                  <App />
                </AuthGate>
              </ConfirmProvider>
            </ToastProvider>
          </LanguageRoot>
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  </React.StrictMode>
);
