import { createContext, useContext } from "react";

// "simple" (default) hides technical detail; "advanced" reveals it. Global,
// not per-screen — matches the mockup's single header toggle.
export const ModeContext = createContext("simple");

export const useMode = () => useContext(ModeContext);
