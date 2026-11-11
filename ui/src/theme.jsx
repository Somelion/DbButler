import { createContext, useContext, useEffect, useState } from "react";

// Light/dark theme. "system" follows the OS (prefers-color-scheme); "light"
// and "dark" pin it. The choice is written to <html data-theme> — see
// styles/tokens.css — and remembered per browser. index.html applies the
// stored value before React loads, so a pinned theme doesn't flash.
export const THEME_STORAGE_KEY = "pgdba.theme";
const THEMES = ["system", "light", "dark"];

const ThemeContext = createContext(null);

function readStoredTheme() {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return THEMES.includes(stored) ? stored : "system";
  } catch {
    return "system";
  }
}

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(readStoredTheme);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // Storage blocked (private mode) — the choice just won't persist.
    }
  }, [theme]);

  return <ThemeContext.Provider value={{ theme, setTheme }}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme must be used inside <ThemeProvider>");
  return value;
}
