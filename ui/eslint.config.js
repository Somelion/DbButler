// ESLint flat config. eslint-plugin-react and eslint-plugin-jsx-a11y haven't
// declared ESLint 10 support yet, so their rules run through @eslint/compat's
// fixupPluginRules (and package.json "overrides" accepts their peer range).
import { fixupPluginRules } from "@eslint/compat";
import js from "@eslint/js";
import jsxA11y from "eslint-plugin-jsx-a11y";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";

export default [
  { ignores: ["dist/**", "node_modules/**"] },
  js.configs.recommended,
  {
    files: ["**/*.{js,jsx}"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: { ...globals.browser },
    },
    settings: { react: { version: "detect" } },
    plugins: {
      react: fixupPluginRules(react),
      "react-hooks": reactHooks,
      "jsx-a11y": fixupPluginRules(jsxA11y),
    },
    rules: {
      ...react.configs.recommended.rules,
      ...react.configs["jsx-runtime"].rules,
      ...reactHooks.configs.recommended.rules,
      ...jsxA11y.flatConfigs.recommended.rules,
      // Plain JS, no TypeScript — prop-types adds nothing here.
      "react/prop-types": "off",
      // Apostrophes in JSX text ("don't") render fine; escaping them all
      // would only make the copy harder to read.
      "react/no-unescaped-entities": "off",
      "no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },
  {
    files: ["vite.config.js", "eslint.config.js"],
    languageOptions: { globals: { ...globals.node } },
  },
];
