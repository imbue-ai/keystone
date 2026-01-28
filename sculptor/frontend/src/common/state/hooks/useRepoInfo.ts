import { useAtomValue, useStore } from "jotai";
import { useCallback } from "react";

import type { RepoInfo } from "../../../api";
import { getCurrentBranch, getRepoInfo } from "../../../api";
import type { ProjectID } from "../../Types";
import { repoInfoAtomFamily } from "../atoms/repoInfo.ts";

type RepoInfoHookReturn = {
  /** Current repository information for this project */
  repoInfo: RepoInfo | null;
  /** Function to refetch repository information from the server */
  fetchRepoInfo: () => Promise<RepoInfo | undefined>;
  /** Function to fetch just the current branch */
  fetchCurrentBranch: () => Promise<void>;
};

export const useRepoInfo = (projectId: ProjectID): RepoInfoHookReturn => {
  const store = useStore();
  const repoInfo = useAtomValue(repoInfoAtomFamily(projectId));

  const fetchRepoInfo = useCallback(async (): Promise<RepoInfo | undefined> => {
    try {
      const { data: repoInfo } = await getRepoInfo({
        path: { project_id: projectId },
        meta: { skipWsAck: true },
      });

      // Update the atom for this project
      const repoInfoAtom = repoInfoAtomFamily(projectId);
      store.set(repoInfoAtom, repoInfo);

      return repoInfo;
    } catch (error) {
      console.error(`Failed to load repo info for project ${projectId}:`, error);
    }
  }, [projectId, store]);

  const fetchCurrentBranch = useCallback(async (): Promise<void> => {
    try {
      const { data: currentBranchInfo } = await getCurrentBranch({
        path: { project_id: projectId },
        meta: { skipWsAck: true },
      });

      const repoInfoAtom = repoInfoAtomFamily(projectId);
      const existingRepoInfo = store.get(repoInfoAtom);

      const newRepoInfo: RepoInfo = {
        currentBranch: currentBranchInfo.currentBranch,
        numUncommittedChanges: currentBranchInfo.numUncommittedChanges,
        projectId: projectId,
        recentBranches: existingRepoInfo?.recentBranches || [],
        repoPath: existingRepoInfo?.repoPath || "",
      };

      store.set(repoInfoAtom, newRepoInfo);
    } catch (error) {
      console.error(`Failed to load current branch for project ${projectId}:`, error);
    }
  }, [projectId, store]);

  return {
    repoInfo,
    fetchRepoInfo,
    fetchCurrentBranch,
  };
};
