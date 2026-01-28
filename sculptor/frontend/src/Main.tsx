import "@radix-ui/themes/styles.css";
import "./index.css";

import React from "react";
import ReactDOM from "react-dom/client";

import { configureClient } from "./apiClient.ts";
import { App } from "./App.tsx";
import { initializeSessionToken } from "./common/Auth.ts";
import { initializeSentry } from "./instrument.ts";

(async (): Promise<void> => {
  try {
    initializeSentry();
    await configureClient();
    await initializeSessionToken();
  } catch (e) {
    console.log("Initialization failed", e);
  }

  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
})();
