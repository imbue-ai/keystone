export const LOGIN_ENDPOINT = "/api/v1/auth/login";
export const LOGOUT_ENDPOINT = "/api/v1/auth/logout";
export const RENEW_TOKENS_ENDPOINT = "/api/v1/auth/renew-tokens";
const SESSION_TOKEN_ENDPOINT = "api/v1/session-token";

import { atom, createStore } from "jotai";

import type { RefreshData, TokenPair } from "../api";

// Used to trigger a dialog when an auth error occurs.
// We need to use a separate store because there's no way for
// the logic in Endpoints.ts to access the default store
// declared by the JotaiProvider.
//
// FIXME (hynek): This isn't entirely right because it breaks the isolation that JotaiProvider gives us.
// I don't know how to do it at the moment, though, so let's just see what happens.
export const authStore = createStore();
export const authRequiredModalAtom = atom(false);
export const isLoggingOutAtom = atom(false);

const ACCESS_TOKEN_SESSION_STORAGE_KEY = "sculptor-access-token";
const REFRESH_TOKEN_SESSION_STORAGE_KEY = "sculptor-refresh-token";
export const SESSION_TOKEN_HEADER_NAME = "x-session-token";

// We use direct sessionStorage access instead of jotai atomWithStorage.
// The async initialization of jotai was causing issues with the initial render.
// We don't need to reactively update the token in the UI, so this is fine.
//
// Also, we don't use persistent localStorage because we want to prevent
// tokens from being created in one sculptor instance and then reused
// in another instance (when you run multiple sculptors for multiple
// projects). That way the behavior remains more predictable.

export const getAccessToken = (): string | null => {
  return sessionStorage.getItem(ACCESS_TOKEN_SESSION_STORAGE_KEY);
};

export const setAccessToken = (token: string): void => {
  sessionStorage.setItem(ACCESS_TOKEN_SESSION_STORAGE_KEY, token);
};

export const getRefreshToken = (): string | null => {
  return sessionStorage.getItem(REFRESH_TOKEN_SESSION_STORAGE_KEY);
};

export const setRefreshToken = (token: string): void => {
  sessionStorage.setItem(REFRESH_TOKEN_SESSION_STORAGE_KEY, token);
};

export const forgetTokens = (): void => {
  sessionStorage.removeItem(ACCESS_TOKEN_SESSION_STORAGE_KEY);
  sessionStorage.removeItem(REFRESH_TOKEN_SESSION_STORAGE_KEY);
};

export const getLoginURL = (): string => {
  const encodedPathname = encodeURIComponent(window.location.pathname);
  return LOGIN_ENDPOINT + `?next_path=${encodedPathname}`;
};

// Avoid multiple concurrent refresh requests.
// (This is important because a refresh token can only be used once.)
let refreshTokenPromise: Promise<boolean> | undefined = undefined;

export const maybeRefreshToken = async (): Promise<boolean> => {
  if (refreshTokenPromise) {
    return refreshTokenPromise;
  }

  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    refreshTokenPromise = undefined;
    return false;
  }

  const refreshData: RefreshData = {
    refreshToken: refreshToken,
  };
  refreshTokenPromise = fetch(RENEW_TOKENS_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(refreshData),
  })
    .then(async (response) => {
      if (!response.ok) {
        // Typically: refresh token expired.
        return false;
      }
      const data: TokenPair = await response.json();
      setAccessToken(data.accessToken);
      setRefreshToken(data.refreshToken);
      return true;
    })
    .finally(() => {
      refreshTokenPromise = undefined;
    });

  return refreshTokenPromise;
};

let sessionToken: string | undefined = undefined;

/*
 * Initialize the session token - serves as a CSRF protection mechanism.
 */
export const initializeSessionToken = async (): Promise<void> => {
  if (!window.sculptor) {
    // As a backup, outside of the electron context, initialize the session token using through the samesite cookie.
    const sessionTokenInitializationURL = new URL(SESSION_TOKEN_ENDPOINT, API_URL_BASE || window.location.origin);
    // This sets the session token cookie.
    await fetch(sessionTokenInitializationURL.toString(), { method: "GET" });
  } else {
    sessionToken = await window.sculptor.getSessionToken();
  }
};

export const getSessionToken = (): string | undefined => {
  return sessionToken;
};

export const setupAuthHeaders = (headers: Headers): undefined => {
  const accessToken = getAccessToken();
  if (accessToken) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }
  const sessionToken = getSessionToken();
  if (sessionToken) {
    headers.set(SESSION_TOKEN_HEADER_NAME, sessionToken);
  }
};
