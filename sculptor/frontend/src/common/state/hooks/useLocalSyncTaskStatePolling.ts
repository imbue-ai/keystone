import { useAtom, useAtomValue, useSetAtom } from "jotai";
import { useCallback, useEffect, useMemo, useRef } from "react";

import type { SyncedTaskView } from "../../../api";
import { getGlobalSyncStateStopgap, LocalSyncStatus } from "../../../api";
import type { LocalSyncState } from "../atoms/localSyncState";
import { localSyncStateAtom } from "../atoms/localSyncState";
import { sculptorStashSingletonStateAtom } from "../atoms/sculptorStashSingleton";
import { useTasks } from "./useTaskHelpers";

const POLLING_INTERVAL_MS = 3000; // 3 seconds

const useLocalSyncedTaskInCurrentProject = (projectID: string): SyncedTaskView | undefined => {
  const { tasks } = useTasks(projectID);
  return useMemo(() => {
    return tasks.find((task) => task.sync.status !== LocalSyncStatus.INACTIVE);
  }, [tasks]);
};

const usePollingEffect = (fetchingCallback: () => void, interval: number): void => {
  const intervalRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    fetchingCallback();
    intervalRef.current = setInterval(fetchingCallback, interval);

    return (): void => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
    };
  }, [fetchingCallback, interval]);
};

/**
 * Hook that polls the global sync state unless there is a current in-project sync.
 * Returns nothing - use
 */
export const useLocalSyncTaskStatePolling = ({ currentProjectID }: { currentProjectID: string }): undefined => {
  const setLocalSyncState = useAtom(localSyncStateAtom)[1];
  const setSculptorStashSingletonState = useSetAtom(sculptorStashSingletonStateAtom);

  // NOTE: Prefer the in-project task if we have a race because the global is polling and thus could be outdated
  const syncedTaskInThisProject = useLocalSyncedTaskInCurrentProject(currentProjectID);
  const isThisProjectSyncedThusPreemptingPolling = syncedTaskInThisProject !== undefined;

  const fetchSyncState = useCallback(async () => {
    try {
      const { data } = await getGlobalSyncStateStopgap();

      if (!data) {
        console.warn(
          "No data received from getGlobalSyncStateStopgap! Should get empty object if no sync or stash is present",
        );
        return;
      }
      const { syncedTask, stashSingleton } = data;
      const isOtherProjectSynced = syncedTask !== null && syncedTask.projectId !== currentProjectID;
      const isOtherProjectStashed = stashSingleton !== null && stashSingleton.projectId !== currentProjectID;

      // TODO(mjr): Now that we stream all task updates across all projects the whole state polling thing can be diced into a substream I think
      // The useEffect below will handle setting the in-project sync state eagerly if we're streaming it already
      if (!isThisProjectSyncedThusPreemptingPolling) {
        setLocalSyncState(syncedTask ? { syncedTask, isOtherProjectSynced } : null);
      }
      setSculptorStashSingletonState(stashSingleton ? { stashSingleton, isOtherProjectStashed } : null);
    } catch (error) {
      // On error, just hope next poll works I guess
      // TODO: Need to actually notify user of errors outside toast
      console.error("Failed to fetch global sync state:", error);
    }
  }, [isThisProjectSyncedThusPreemptingPolling, currentProjectID, setLocalSyncState, setSculptorStashSingletonState]);

  usePollingEffect(fetchSyncState, POLLING_INTERVAL_MS);

  // not critical path due to some race conditions but better to be consistent.
  // TODO(mjr): need to factor all polling into stream
  useEffect(() => {
    if (isThisProjectSyncedThusPreemptingPolling) {
      // rely on immediate effect
      setLocalSyncState({
        syncedTask: syncedTaskInThisProject,
        isOtherProjectSynced: false,
      });
    }
  }, [syncedTaskInThisProject, isThisProjectSyncedThusPreemptingPolling, setLocalSyncState]);
};

export const useLocalSyncState = ({ currentProjectID }: { currentProjectID: string }): LocalSyncState | null => {
  const localSyncState = useAtomValue(localSyncStateAtom);

  const syncedTaskInThisProject = useLocalSyncedTaskInCurrentProject(currentProjectID);
  const isThisProjectSyncedThusPreemptingPolling = syncedTaskInThisProject !== undefined;

  if (isThisProjectSyncedThusPreemptingPolling) {
    return {
      syncedTask: syncedTaskInThisProject,
      isOtherProjectSynced: false,
    };
  }
  return localSyncState;
};
