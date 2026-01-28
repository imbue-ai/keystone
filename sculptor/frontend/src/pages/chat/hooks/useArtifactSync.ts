import { useAtomValue, useSetAtom } from "jotai";
import { useCallback, useEffect, useRef } from "react";

import { ArtifactType, getArtifactData, type GetArtifactDataResponse } from "../../../api";
import {
  isDiffArtifact,
  isLogsArtifact,
  isSuggestionsArtifact,
  isTodoListArtifact,
  isUsageArtifact,
} from "../../../common/Guards";
import {
  clearTaskUpdatedArtifactsAtom,
  taskUpdatedArtifactsAtomFamily,
  updateTaskDetailAtom,
} from "../../../common/state/atoms/taskDetails";
import type { ArtifactsMap } from "../Types";

/**
 * Hook that watches for artifact updates in the task detail stream
 * and fetches them via HTTP.
 *
 * This is separated from the main stream processing because:
 * 1. Artifacts are large and shouldn't be fetched for background tasks
 * 2. HTTP fetching is async and separate from the WebSocket stream
 */
export const useArtifactSync = (projectId: string, taskId: string): void => {
  const updateTaskDetail = useSetAtom(updateTaskDetailAtom);
  const clearTaskUpdatedArtifacts = useSetAtom(clearTaskUpdatedArtifactsAtom);
  const updatedArtifacts = useAtomValue(taskUpdatedArtifactsAtomFamily(taskId));

  // Track which artifacts are currently being fetched to avoid duplicate requests
  const inFlightArtifacts = useRef<Set<ArtifactType>>(new Set());

  const fetchArtifact = useCallback(
    async (artifactType: ArtifactType): Promise<void> => {
      // Skip if already fetching this artifact
      if (inFlightArtifacts.current.has(artifactType)) {
        return;
      }
      inFlightArtifacts.current.add(artifactType);

      try {
        const { data } = await getArtifactData({
          path: { project_id: projectId, task_id: taskId, artifact_name: artifactType },
        });

        if (!data) {
          console.error(`Error fetching artifact ${artifactType}: no data returned`);
          return;
        }

        const processedData = processArtifactResponse(data, artifactType);

        updateTaskDetail({
          taskId,
          updater: (currentState) => {
            if (!currentState) {
              // If no state exists, skip artifact update (shouldn't happen in practice)
              console.warn(`No task detail state found for task ${taskId}, skipping artifact update`);
              return currentState!;
            }
            return {
              ...currentState,
              artifacts: {
                ...currentState.artifacts,
                [artifactType]: processedData,
              },
            };
          },
        });
      } catch (error) {
        console.error(`Error fetching artifact ${artifactType}:`, error);
      } finally {
        inFlightArtifacts.current.delete(artifactType);
        clearTaskUpdatedArtifacts({ taskId, artifactTypes: [artifactType] });
      }
    },
    [projectId, taskId, updateTaskDetail, clearTaskUpdatedArtifacts],
  );

  // Watch for updated artifacts and fetch them
  useEffect(() => {
    if (updatedArtifacts.length > 0) {
      updatedArtifacts.forEach((artifactType) => {
        fetchArtifact(artifactType);
      });
    }
  }, [updatedArtifacts, fetchArtifact]);

  // Reset requested artifacts when task changes
  useEffect(() => {
    inFlightArtifacts.current.clear();
  }, [taskId]);
};

const processArtifactResponse = (
  response: GetArtifactDataResponse,
  artifactType: ArtifactType,
): ArtifactsMap[keyof ArtifactsMap] => {
  if (isDiffArtifact(response) && artifactType === ArtifactType.DIFF) {
    return response;
  }

  if (isTodoListArtifact(response) && artifactType === ArtifactType.PLAN) {
    return response;
  }

  if (isLogsArtifact(response) && artifactType === ArtifactType.LOGS) {
    return response;
  }

  if (isSuggestionsArtifact(response) && artifactType === ArtifactType.SUGGESTIONS) {
    return response;
  }

  if (isUsageArtifact(response) && artifactType === ArtifactType.USAGE) {
    return response;
  }

  throw new Error(`Artifact type mismatch: expected ${artifactType}, got ${response.objectType}`);
};
