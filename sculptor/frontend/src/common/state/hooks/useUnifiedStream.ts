import { useSetAtom } from "jotai";
import { useCallback } from "react";

import { ArtifactType, type StreamingUpdate } from "../../../api";
import { updateLocalRepoInfoAtom } from "../atoms/localRepoInfo";
import { notificationsAtom } from "../atoms/notifications";
import { updateProjectsAtom } from "../atoms/projects";
import { sculptorSettingsAtom } from "../atoms/sculptorSettings";
import { getEmptyTaskDetailState, updateTaskDetailAtom, updateTaskUpdatedArtifactsAtom } from "../atoms/taskDetails";
import { updateTasksAtom } from "../atoms/tasks";
import { acknowledgeRequests, updateActiveWebsockets } from "../requestTracking";
import {
  chatMessagesReducer,
  checkOutputMessagesReducer,
  checkUpdateMessagesReducer,
  feedbackReducer,
  logsReducer,
} from "../taskDetailReducers.ts";
import { useWebsocket } from "./useWebsocket";

const API_BASE_URL = "/api/v1";

/**
 * This hook:
 * 1. Connects to the unified WebSocket stream
 * 2. Processes task view updates (for sidebar/task list)
 * 3. Processes task detail updates for ALL tasks (even background ones)
 * 4. Processes user updates (projects, settings, repo info)
 * 5. Handles request tracking acknowledgments
 *
 * Task details are accumulated in global atoms so switching between tasks
 * doesn't lose state.
 */
export const useUnifiedStream = (): void => {
  const updateTasks = useSetAtom(updateTasksAtom);
  const updateProjects = useSetAtom(updateProjectsAtom);
  const setNotifications = useSetAtom(notificationsAtom);
  const setSculptorSettings = useSetAtom(sculptorSettingsAtom);
  const updateTaskDetail = useSetAtom(updateTaskDetailAtom);
  const updateTaskUpdatedArtifacts = useSetAtom(updateTaskUpdatedArtifactsAtom);
  const updateLocalRepoInfo = useSetAtom(updateLocalRepoInfoAtom);

  const onOpen = useCallback(() => {
    updateActiveWebsockets(true);
  }, []);

  const onClose = useCallback(() => {
    updateActiveWebsockets(false);
  }, []);

  const onMessage = useCallback(
    (data: StreamingUpdate): void => {
      // ========================================================================
      // Handle task views (for task list/sidebar)
      // ========================================================================
      if (data.taskViewsByTaskId) {
        updateTasks(data.taskViewsByTaskId);
      }

      // ========================================================================
      // Handle task details (for chat pages)
      //    Process ALL tasks, even if not currently viewing them
      // NOTE: This is O(activeTasks) (as opposed to also archived) because we only get a task update if something happens
      // ========================================================================
      if (data.taskUpdateByTaskId && Object.keys(data.taskUpdateByTaskId).length > 0) {
        Object.entries(data.taskUpdateByTaskId).forEach(([taskId, taskUpdate]) => {
          updateTaskDetail({
            taskId,
            updater: (currentState) => {
              const state = currentState || getEmptyTaskDetailState();

              // Process incremental updates using pure reducers
              const newChatState = chatMessagesReducer(
                {
                  completedChatMessages: state.completedChatMessages,
                  inProgressChatMessage: state.inProgressChatMessage,
                  queuedChatMessages: state.queuedChatMessages,
                  workingUserMessageId: state.workingUserMessageId,
                },
                taskUpdate,
              );

              const newCheckOutputsState = checkOutputMessagesReducer(
                {
                  checkOutputListByMessageId: state.checkOutputListByMessageId,
                  newCheckOutputs: state.artifacts[ArtifactType.NEW_CHECK_OUTPUTS] ?? null,
                },
                taskUpdate,
              );

              const newChecksState = checkUpdateMessagesReducer(
                {
                  checksData: state.checksData,
                  checksDefinedForMessage: state.checksDefinedForMessage,
                },
                taskUpdate,
              );

              const previousLogs = state.artifacts[ArtifactType.LOGS]?.logs ?? [];
              const newLogsState = logsReducer({ logs: previousLogs }, taskUpdate);

              const newFeedbackState = feedbackReducer({ feedbackByMessageId: state.feedbackByMessageId }, taskUpdate);

              // Merge all processed state
              const hasLogsChanged = newLogsState.logs !== previousLogs;
              const currentCheckOutputs = state.artifacts[ArtifactType.NEW_CHECK_OUTPUTS] ?? null;
              const hasCheckOutputsChanged = newCheckOutputsState.newCheckOutputs !== currentCheckOutputs;

              let nextArtifacts = state.artifacts;

              if (hasLogsChanged || hasCheckOutputsChanged) {
                nextArtifacts = { ...state.artifacts };

                if (hasLogsChanged) {
                  nextArtifacts[ArtifactType.LOGS] = {
                    logs: newLogsState.logs,
                  };
                }

                if (hasCheckOutputsChanged) {
                  if (newCheckOutputsState.newCheckOutputs) {
                    nextArtifacts[ArtifactType.NEW_CHECK_OUTPUTS] = newCheckOutputsState.newCheckOutputs;
                  } else {
                    delete nextArtifacts[ArtifactType.NEW_CHECK_OUTPUTS];
                  }
                }
              }

              return {
                ...state,
                ...newChatState,
                ...newChecksState,
                ...newFeedbackState,
                checkOutputListByMessageId: newCheckOutputsState.checkOutputListByMessageId,
                artifacts: nextArtifacts,
              };
            },
          });

          // Track which artifacts need fetching
          if (taskUpdate.updatedArtifacts && taskUpdate.updatedArtifacts.length > 0) {
            updateTaskUpdatedArtifacts({
              taskId,
              artifactTypes: taskUpdate.updatedArtifacts,
            });
          }
        });
      }

      // ========================================================================
      // Handle user update
      // ========================================================================
      if (data.userUpdate) {
        const userUpdate = data.userUpdate;

        if (userUpdate.notifications && userUpdate.notifications.length > 0) {
          setNotifications(userUpdate.notifications);
        }

        if (userUpdate.projects && userUpdate.projects.length > 0) {
          const activeProjects = userUpdate.projects.filter((p) => !p.isDeleted);
          updateProjects(activeProjects);
        }

        if (userUpdate.settings) {
          setSculptorSettings(userUpdate.settings);
        }
      }

      // ========================================================================
      // Handle local repo info updates
      // ========================================================================
      if (data.localRepoInfoByProjectId && Object.keys(data.localRepoInfoByProjectId).length > 0) {
        Object.entries(data.localRepoInfoByProjectId).forEach(([projectId, repoInfo]) => {
          updateLocalRepoInfo({ projectId, repoInfo: repoInfo ?? null });
        });
      }

      // ========================================================================
      // 4. Handle finished request IDs
      // ========================================================================
      if (data.finishedRequestIds && data.finishedRequestIds.length > 0) {
        acknowledgeRequests(data.finishedRequestIds);
      }
    },
    [
      updateTasks,
      updateProjects,
      setNotifications,
      setSculptorSettings,
      updateTaskDetail,
      updateTaskUpdatedArtifacts,
      updateLocalRepoInfo,
    ],
  );

  useWebsocket<StreamingUpdate>({
    url: `${API_BASE_URL}/stream/ws`,
    onOpen,
    onClose,
    onMessage,
  });
};
