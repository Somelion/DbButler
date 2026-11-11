import { createContext, useContext } from "react";

// "simple" (default) hides technical detail; "advanced" reveals it. Global,
// not per-screen — matches the mockup's single header toggle. App owns the
// state and remembers it across reloads (MODE_STORAGE_KEY).
export const ModeContext = createContext("simple");

export const MODE_STORAGE_KEY = "pgdba.mode";

export const useMode = () => useContext(ModeContext);
