import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { initEnv } from "./lib/env";
import "./styles/app.css";

initEnv().then(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App />
    </StrictMode>
  );
});
