import { ErrorBoundary } from "@sentry/react";
import { Provider as JotaiProvider } from "jotai/react";
import { posthog } from "posthog-js";
import { PostHogProvider } from "posthog-js/react";
import type { ReactElement } from "react";
import { useRef } from "react";
import { useEffect } from "react";
import { useState } from "react";

import { getTelemetryInfo } from "~/api";
import { initializeTelemetry } from "~/common/Telemetry.ts";

import { AuthRequiredModal } from "./components/AuthRequiredModal.tsx";
import { AutoUpdaterNotification } from "./components/AutoUpdaterNotification.tsx";
import { BackendStatusBoundary } from "./components/BackendStatusBoundary.tsx";
import { ConfigLoader } from "./components/ConfigLoader.tsx";
import { RequireOnboarding } from "./components/RequireOnboarding.tsx";
import { ThemeProvider } from "./components/ThemeProvider.tsx";
import { ToastProvider } from "./components/Toast.tsx";
import { TokenCapture } from "./components/TokenCapture.tsx";
import { ErrorPage } from "./pages/error/ErrorPage.tsx";
import { Router } from "./Router.tsx";

export const App = (): ReactElement => {
  // Wait with router initialization until the token is captured.
  // (That way we avoid sending unauthenticated requests to the API when we actually already have a token.)
  const [isTokenReady, setIsTokenReady] = useState(false);
  const [isBackendAPIReady, setIsBackendAPIReady] = useState(false);
  const isPosthogInitialized = useRef<boolean>(false);

  const initializePosthog = async (): Promise<void> => {
    const { data: telemetryInfo } = await getTelemetryInfo({
      meta: {
        skipWsAck: true,
      },
    });

    if (isPosthogInitialized.current) return;
    if (telemetryInfo) {
      console.log("Initializing telemetry.");
      initializeTelemetry(telemetryInfo);
      isPosthogInitialized.current = true;
    }
  };

  useEffect(() => {
    if (isPosthogInitialized.current) return;
    if (!isTokenReady || !isBackendAPIReady) return;

    // NOTE: no retrying on failure here
    initializePosthog();
  }, [isTokenReady, isBackendAPIReady]);

  return (
    <ErrorBoundary fallback={(props) => <ErrorPage error={props.error} />} showDialog>
      <PostHogProvider client={posthog}>
        <JotaiProvider>
          <ThemeProvider>
            <ToastProvider>
              <AuthRequiredModal />
              <TokenCapture onDone={() => setIsTokenReady(true)} />
              <BackendStatusBoundary setIsBackendAPIReady={setIsBackendAPIReady}>
                <RequireOnboarding isTokenReady={isTokenReady}>
                  <ConfigLoader isTokenReady={isTokenReady}>
                    <Router />
                  </ConfigLoader>
                </RequireOnboarding>
              </BackendStatusBoundary>
            </ToastProvider>
            <AutoUpdaterNotification />
          </ThemeProvider>
        </JotaiProvider>
      </PostHogProvider>
    </ErrorBoundary>
  );
};
