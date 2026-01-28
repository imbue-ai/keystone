import { HTTPException, RequestTimeoutError, ValidationError } from "~/common/Errors.ts";

import { client } from "./api/client.gen";
import {
  authRequiredModalAtom,
  authStore,
  getAccessToken,
  maybeRefreshToken,
  setupAuthHeaders,
} from "./common/Auth.ts";
import { createRequestTracker } from "./common/state/requestTracking.ts";
import { makeRequestId } from "./common/Utils.ts";

type TrackingConfig = {
  /** Custom timeout in milliseconds for WebSocket acknowledgment */
  customTimeoutMs?: number;
  /** Whether to skip WebSocket tracking entirely */
  shouldSkipTracking: boolean;
};

/**
 * Internal headers used as a transport mechanism for meta options
 * SKIP_WS_ACK is stripped before the request reaches the backend
 */
const INTERNAL_HEADERS = {
  SKIP_WS_ACK: "Sculptor-Skip-WS-Ack",
  REQUEST_TIMEOUT: "Sculptor-Request-Timeout",
  REQUEST_ID: "Sculptor-Request-ID",
} as const;

/**
 * Custom fetch implementation that adds request tracking and authentication
 *
 * This function:
 * 1. Generates a unique request ID for tracking
 * 2. Sets up authentication headers
 * 3. Creates a request tracker that waits for WebSocket acknowledgment
 * 4. Handles authentication errors with automatic token refresh
 * 5. Ensures proper cleanup on error
 *
 * @param input - The resource to fetch (URL, Request, or string)
 * @param init - Request initialization options
 * @returns A Promise that resolves to the Response
 */
export const makeAPIRequest = async (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
  const requestId = makeRequestId();

  let headers: Headers;
  if (input instanceof Request) {
    // If it's a Request, use its headers (which may have been modified by interceptor)
    headers = new Headers(input.headers);
  } else {
    // Otherwise, use headers from init
    headers = new Headers(init.headers ?? {});
  }

  headers.set(INTERNAL_HEADERS.REQUEST_ID, requestId);

  setupAuthHeaders(headers);
  const trackingConfig = extractTrackingConfig(headers);
  setupContentTypeHeader(headers, init.body);

  const urlString = extractUrlString(input);
  const method = extractMethod(input, init);

  const tracker = trackingConfig.shouldSkipTracking
    ? createNoOpTracker()
    : createRequestTracker(requestId, urlString, method, trackingConfig.customTimeoutMs);

  try {
    let response = await fetch(input, { ...init, headers });

    response = await handleAuthError(response, input, init, headers, tracker.cancel);

    if (!response.ok) {
      tracker.cancel();

      let errorDetail = `HTTP ${response.status}`;
      try {
        const errorData = await response.json();
        if (errorData.detail && typeof errorData.detail === "string") {
          errorDetail = errorData.detail;
        } else if (errorData.status === 422 && errorData.detail && Array.isArray(errorData.detail)) {
          throw new ValidationError(errorData.detail);
        }
      } catch {
        // Failed to parse JSON, use default error message
      }

      throw new HTTPException(response.status, errorDetail);
    }

    await tracker.wait;
    return response;
  } catch (error) {
    tracker.cancel();
    if (error instanceof DOMException && error.name === "TimeoutError") {
      throw new RequestTimeoutError("Request timed out");
    }
    throw error;
  }
};

export let baseUrl: string;

/**
 * Configures the API client with custom fetch and interceptors
 *
 * This function:
 * 1. Sets up the base URL and custom fetch implementation
 * 2. Adds a request interceptor to handle meta options for tracking
 */
export const configureClient = async (): Promise<void> => {
  baseUrl = API_URL_BASE !== undefined ? API_URL_BASE : `http://localhost:${await window.sculptor!.getBackendPort()}`;

  client.setConfig({
    baseUrl,
    fetch: makeAPIRequest,
  });

  // Interceptor converts meta options to headers that our custom fetch can read
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  client.interceptors.request.use((request: Request, options: any) => {
    const meta = options?.meta;

    if (!meta) {
      return request;
    }

    const headers = new Headers(request.headers);

    if (meta.timeout) {
      headers.set(INTERNAL_HEADERS.REQUEST_TIMEOUT, String(meta.timeout));
    }

    if (meta.skipWsAck) {
      headers.set(INTERNAL_HEADERS.SKIP_WS_ACK, "true");
    }

    const signal = meta.timeout ? AbortSignal.timeout(meta.timeout) : null;

    // Create a new Request with the modified headers
    return new Request(request, {
      headers,
      signal,
    });
  });
};

const extractUrlString = (input: RequestInfo | URL): string => {
  if (typeof input === "string") {
    return input;
  }

  if (typeof Request !== "undefined" && input instanceof Request) {
    return input.url;
  }

  if (typeof URL !== "undefined" && input instanceof URL) {
    return input.toString();
  }

  return String(input);
};

const extractMethod = (input: RequestInfo | URL, init?: RequestInit): string | undefined => {
  if (init?.method) {
    return init.method;
  }

  // Method from Request object if no init.method provided
  if (typeof Request !== "undefined" && input instanceof Request) {
    return input.method;
  }

  return undefined;
};

const extractTrackingConfig = (headers: Headers): TrackingConfig => {
  const timeoutHeader = headers.get(INTERNAL_HEADERS.REQUEST_TIMEOUT);
  const customTimeoutMs = timeoutHeader ? parseInt(timeoutHeader, 10) : undefined;
  const shouldSkipTracking = headers.get(INTERNAL_HEADERS.SKIP_WS_ACK) === "true";

  // Clean up internal the header before request goes to backend
  headers.delete(INTERNAL_HEADERS.SKIP_WS_ACK);

  return {
    customTimeoutMs,
    shouldSkipTracking,
  };
};

const setupContentTypeHeader = (headers: Headers, body: unknown): void => {
  const isJsonBody = typeof body === "string";
  const hasContentType = headers.has("Content-Type");

  if (isJsonBody && !hasContentType) {
    headers.set("Content-Type", "application/json");
  }
};

const createNoOpTracker = (): { wait: Promise<void>; cancel: () => void } => {
  return {
    wait: Promise.resolve(),
    cancel: (): void => {},
  };
};

const handleAuthError = async (
  response: Response,
  input: RequestInfo | URL,
  init: RequestInit,
  headers: Headers,
  cancelTracker: () => void,
): Promise<Response> => {
  const isAuthError = response.status === 401 || response.status === 403;

  if (!isAuthError) {
    return response;
  }

  if (response.status === 401) {
    const didRefreshSucceed = await maybeRefreshToken();

    if (!didRefreshSucceed) {
      authStore.set(authRequiredModalAtom, true);
      cancelTracker();
      throw new Error("Unauthorized");
    }
    // Retry with refreshed token
    const newAccessToken = getAccessToken();
    if (newAccessToken) {
      headers.set("Authorization", `Bearer ${newAccessToken}`);
    }
    // TODO: There are cases when this won't work because the original request had a streaming body and is already exhausted (e.g. file upload).
    return fetch(input, { ...init, headers });
  }

  if (response.status === 403 && response.headers.get("x-error-code") === "invalid_session_token") {
    console.error("Invalid session token - cannot recover");
  }
  return response;
};
