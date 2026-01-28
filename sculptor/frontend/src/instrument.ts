import * as Sentry from "@sentry/react";

declare const FRONTEND_SENTRY_DSN: string;
declare const FRONTEND_SENTRY_RELEASE_ID: string;

export const initializeSentry = (): void => {
  if (!FRONTEND_SENTRY_DSN || FRONTEND_SENTRY_DSN === "") {
    console.log("Sentry DSN not configured, skipping initialization");
    return;
  }

  console.log(`Initializing Sentry with DSN: ${FRONTEND_SENTRY_DSN} and release ID: ${FRONTEND_SENTRY_RELEASE_ID}`);

  Sentry.init({
    dsn: FRONTEND_SENTRY_DSN,
    integrations: [
      Sentry.browserTracingIntegration(),
      Sentry.captureConsoleIntegration({
        levels: ["error", "warn"],
      }),
      Sentry.contextLinesIntegration(),
      Sentry.extraErrorDataIntegration(),
      // FIXME: turn this back on, but only once we make custom HTTP error codes for the places where we currently return 500
      // Sentry.httpClientIntegration(),
      // disabling all masking for now
      Sentry.replayIntegration({
        maskAllText: false,
        maskAllInputs: false,
        blockAllMedia: false,
      }),
    ],
    tracesSampleRate: 1.0,
    replaysSessionSampleRate: 1.0,
    replaysOnErrorSampleRate: 1.0,
    environment: import.meta.env.MODE,
    release: FRONTEND_SENTRY_RELEASE_ID,
  });
};
