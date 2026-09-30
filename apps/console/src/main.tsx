import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { applyStoredTheme } from "./theme";

// Apply the stored display preference before the first paint; a failed storage
// read falls back to the light theme instead of blocking the console.
applyStoredTheme();

// https://react.dev/reference/react-dom/client/createRoot
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
