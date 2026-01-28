import { useAtomValue, useSetAtom } from "jotai";
import { useCallback, useEffect, useRef } from "react";

import type { ProviderStatusInfo } from "../../../api";
import { getProviderStatuses, ProviderTag } from "../../../api";
import { providerStatusesAtom } from "../atoms/providerStatuses.ts";

const POLLING_INTERVAL_MS = 20000;

export const useProviderStatusesPoll = (): void => {
  const setProviderStatuses = useSetAtom(providerStatusesAtom);
  const intervalRef = useRef<NodeJS.Timeout | null>(null);

  const fetchStatuses = useCallback(async () => {
    try {
      const { data: providerStatuses } = await getProviderStatuses({
        meta: { skipWsAck: true },
      });

      setProviderStatuses(providerStatuses);
    } catch (error) {
      console.error("Failed to fetch provider statuses:", error);
    }
  }, [setProviderStatuses]);

  useEffect(() => {
    fetchStatuses();
    intervalRef.current = setInterval(fetchStatuses, POLLING_INTERVAL_MS);

    return (): void => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
    };
  }, [fetchStatuses]);
};

export const useDockerStatus = (): ProviderStatusInfo | undefined => {
  const providerStatuses = useAtomValue(providerStatusesAtom);
  return providerStatuses?.find((status) => status.provider === ProviderTag.DOCKER);
};
