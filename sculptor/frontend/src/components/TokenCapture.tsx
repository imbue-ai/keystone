import { useEffect } from "react";

import { setAccessToken, setRefreshToken } from "../common/Auth.ts";

// This component pops the JWT tokens from the URL and stores them in the sessionStorage.
export const TokenCapture = ({ onDone }: { onDone: () => void }): null => {
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    let isURLUpdated = false;

    const accessToken = params.get("accessToken");
    if (accessToken) {
      setAccessToken(accessToken);
      params.delete("accessToken");
      isURLUpdated = true;
    }

    const refreshToken = params.get("refreshToken");
    if (refreshToken) {
      setRefreshToken(refreshToken);
      params.delete("refreshToken");
      isURLUpdated = true;
    }

    if (isURLUpdated) {
      // Change the URL to remove the tokens without reloading the page.
      const newSearch = "?" + params.toString();
      const newUrl = window.location.pathname + newSearch;
      window.history.replaceState({}, "", newUrl);
    }

    onDone();
  }, [onDone]);

  return null;
};
