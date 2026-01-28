import { useCallback, useEffect, useState } from "react";

import { anthropicCredentialsExists, openaiCredentialsExists } from "~/api";

export type ModelCredentials = {
  hasAnthropicCreds: boolean;
  hasOpenAICreds: boolean;
  isLoading: boolean;
  error: Error | null;
  refetch: () => void;
};

/**
 * Custom hook to check and track model credential availability
 * @returns Object containing credential states, loading/error states, and refetch function
 */
export const useModelCredentials = (): ModelCredentials => {
  const [hasAnthropicCreds, setHasAnthropicCreds] = useState(false);
  const [hasOpenAICreds, setHasOpenAICreds] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const checkCredentials = useCallback(async (): Promise<void> => {
    try {
      setIsLoading(true);
      const [anthropicResult, openaiResult] = await Promise.all([
        anthropicCredentialsExists({ meta: { skipWsAck: true } }),
        openaiCredentialsExists({ meta: { skipWsAck: true } }),
      ]);
      setHasAnthropicCreds(anthropicResult.data ?? false);
      setHasOpenAICreds(openaiResult.data ?? false);
      setError(null);
    } catch (err) {
      console.error("Failed to check credentials:", err);
      setError(err instanceof Error ? err : new Error("Failed to check credentials"));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    checkCredentials();
  }, [checkCredentials]);

  return {
    hasAnthropicCreds,
    hasOpenAICreds,
    isLoading,
    error,
    refetch: checkCredentials,
  };
};
