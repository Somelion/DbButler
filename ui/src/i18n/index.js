import i18n from "i18next";
import { initReactI18next } from "react-i18next";

// UI languages. Translations live in ./locales/<language>/<namespace>.json —
// one namespace per screen or area (common, nav, tableHealth, ...), picked
// up here automatically, so adding a namespace is just adding its two files.
//
//   const { t } = useTranslation("tableHealth");
//   t("analyzeAll.label")                       // nested keys
//   t("tables", { count: n })                    // plurals: tables_one / tables_other
//   t("common:actions.cancel")                   // another namespace
//
// Texts the backend generates (finding titles, summaries, fixes) are not
// translated here; they arrive in English.

export const LANGUAGES = [
  { code: "en", label: "EN", name: "English" },
  { code: "et", label: "ET", name: "Eesti" },
];
const SUPPORTED = new Set(LANGUAGES.map((l) => l.code));
const STORAGE_KEY = "dbbutler.language";

const modules = import.meta.glob("./locales/*/*.json", { eager: true, import: "default" });
const resources = {};
for (const [path, messages] of Object.entries(modules)) {
  const [, language, namespace] = path.match(/\.\/locales\/([^/]+)\/([^/]+)\.json$/);
  resources[language] ??= {};
  resources[language][namespace] = messages;
}

function storedLanguage() {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

// The saved choice, else the browser's preferred languages (an Estonian
// browser starts in Estonian), else English.
function initialLanguage() {
  const stored = storedLanguage();
  if (SUPPORTED.has(stored)) return stored;
  const preferred = typeof navigator !== "undefined" ? (navigator.languages ?? [navigator.language]) : [];
  for (const tag of preferred) {
    const base = String(tag || "").toLowerCase().split("-")[0];
    if (SUPPORTED.has(base)) return base;
  }
  return "en";
}

i18n.use(initReactI18next).init({
  resources,
  lng: initialLanguage(),
  fallbackLng: "en",
  supportedLngs: [...SUPPORTED],
  ns: Object.keys(resources.en ?? {}),
  defaultNS: "common",
  // React escapes what it renders.
  interpolation: { escapeValue: false },
  returnNull: false,
});

function syncDocumentLanguage(language) {
  if (typeof document !== "undefined") document.documentElement.lang = language;
}
syncDocumentLanguage(i18n.language);
i18n.on("languageChanged", syncDocumentLanguage);

export function setLanguage(language) {
  if (!SUPPORTED.has(language)) return;
  try {
    localStorage.setItem(STORAGE_KEY, language);
  } catch {
    // Storage blocked: the choice lasts until the tab closes.
  }
  i18n.changeLanguage(language);
}

export default i18n;
