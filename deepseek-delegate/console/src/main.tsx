import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";

// https://react.dev/reference/react-dom/client/createRoot
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
