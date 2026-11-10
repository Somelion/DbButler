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

      // Known debt, tracked as warnings so CI stays green until the UI
      // foundation sprint fixes them (databank review §6.2/§6.3): clickable
      // div/span/tr elements instead of buttons, unlabelled form controls,
      // and data fetching that sets state synchronously inside effects
      // (to be replaced by a shared useApi/usePolling hook). Flip these back
      // to "error" once that sprint lands.
      "jsx-a11y/click-events-have-key-events": "warn",
      "jsx-a11y/no-static-element-interactions": "warn",
      "jsx-a11y/no-noninteractive-element-interactions": "warn",
      "jsx-a11y/label-has-associated-control": "warn",
      "jsx-a11y/no-autofocus": "warn",
      "react-hooks/set-state-in-effect": "warn",
    },
  },
  {
    files: ["vite.config.js", "eslint.config.js"],
    languageOptions: { globals: { ...globals.node } },
  },
];
