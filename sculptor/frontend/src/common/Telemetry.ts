/** Contains utility functions for working with telemetry data. */

import * as Sentry from "@sentry/react";
import type { PostHog, PostHogConfig } from "posthog-js";
import { posthog } from "posthog-js";

import { type TelemetryInfo } from "~/api";

function createPostHogConfig(telemetryInfo: TelemetryInfo): Partial<PostHogConfig> {
  const { userConfig, posthogApiHost } = telemetryInfo;

  return {
    api_host: posthogApiHost,
    capture_pageview: userConfig.isProductAnalyticsEnabled,
    capture_pageleave: userConfig.isProductAnalyticsEnabled,
    autocapture: userConfig.isProductAnalyticsEnabled,
    disable_session_recording: !userConfig.isSessionRecordingEnabled,
    capture_exceptions: false, // Needed for compatibility with Sentry integration
    debug: userConfig.userEmail.toLocaleLowerCase().endsWith("@imbue.com"),
  };
}

export function initializeTelemetry(telemetryInfo: TelemetryInfo): void {
  const { posthogToken, userConfig, sculptorVersion } = telemetryInfo;

  posthog.init(posthogToken, {
    ...createPostHogConfig(telemetryInfo),
    loaded: (posthog: PostHog) => {
      posthog.register({
        source: "sculptor_frontend",
        sculptor_version: sculptorVersion,
        session: {
          instance_id: userConfig.instanceId,
          execution_instance_id: telemetryInfo.sculptorExecutionInstanceId,
        },
      });
      // Important: call .identify after .register to make sure that the $identify event includes the properties above.
      //
      // NOTE: We do not attach any person properties from the frontend, the `email` and any aliasing of this user
      //       identity with the past is handled by the backend.
      posthog.identify(userConfig.userId);
      console.log(`PostHog telemetry SDK initialized for ${userConfig.userId}.`);
    },
  });

  if (userConfig.isErrorReportingEnabled) {
    Sentry.addIntegration(
      posthog.sentryIntegration({
        organization: "Imbue",
        projectId: 136453, // oops
      }),
    );
  }

  Sentry.setUser({
    id: userConfig.userId,
    email: userConfig.userEmail,
  });
}

export function updateTelemetryConfig(telemetryInfo: TelemetryInfo): void {
  posthog.set_config(createPostHogConfig(telemetryInfo));

  // Update Sentry user info in case we updated the email or the User ID
  const { userConfig } = telemetryInfo;
  Sentry.setUser({
    id: userConfig.userId,
    email: userConfig.userEmail,
  });
}
