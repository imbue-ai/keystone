import type { PrimitiveAtom } from "jotai";
import { atom } from "jotai";
import { atomFamily } from "jotai/utils";

import type { ArtifactType, ChatMessage } from "../../../api";
import type { ArtifactsMap, CheckHistory, CheckOutputList } from "../../../pages/chat/Types";

/**
 * Complete state for a single task's detail view.
 * This is accumulated from incremental TaskUpdate messages.
 */
export type TaskDetailState = {
  // Chat messages
  completedChatMessages: Array<ChatMessage>;
  inProgressChatMessage: ChatMessage | null;
  queuedChatMessages: Array<ChatMessage>;
  workingUserMessageId: string | null;
  // Artifacts
  artifacts: ArtifactsMap;
  // Checks
  checksData: Record<string, Record<string, CheckHistory>>;
  checksDefinedForMessage: Set<string>;
  checkOutputListByMessageId: Record<string, CheckOutputList>;
  // Feedback
  feedbackByMessageId: Record<string, string>;
  // Error state
  error?: string;
};

export const taskDetailAtomFamily = atomFamily<string, PrimitiveAtom<TaskDetailState | null>>(() =>
  atom<TaskDetailState | null>(null),
);

export const getEmptyTaskDetailState = (): TaskDetailState => {
  return {
    completedChatMessages: [],
    inProgressChatMessage: null,
    queuedChatMessages: [],
    workingUserMessageId: null,
    artifacts: {},
    checksData: {},
    checksDefinedForMessage: new Set(),
    checkOutputListByMessageId: {},
    feedbackByMessageId: {},
  };
};

export const updateTaskDetailAtom = atom(
  null,
  (getAtom, setAtom, update: { taskId: string; updater: (prev: TaskDetailState | null) => TaskDetailState }) => {
    const currentState = getAtom(taskDetailAtomFamily(update.taskId));
    const newState = update.updater(currentState);
    setAtom(taskDetailAtomFamily(update.taskId), newState);
  },
);

export const taskUpdatedArtifactsAtomFamily = atomFamily<string, PrimitiveAtom<Array<ArtifactType>>>(() =>
  atom<Array<ArtifactType>>([]),
);

export const updateTaskUpdatedArtifactsAtom = atom(
  null,
  (getAtom, setAtom, update: { taskId: string; artifactTypes: Array<ArtifactType> }) => {
    const existing = getAtom(taskUpdatedArtifactsAtomFamily(update.taskId));
    if (existing.length === 0) {
      setAtom(taskUpdatedArtifactsAtomFamily(update.taskId), Array.from(new Set(update.artifactTypes)));
      return;
    }

    const mergedTypes = Array.from(new Set([...existing, ...update.artifactTypes]));
    setAtom(taskUpdatedArtifactsAtomFamily(update.taskId), mergedTypes);
  },
);

export const clearTaskUpdatedArtifactsAtom = atom(
  null,
  (getAtom, setAtom, update: { taskId: string; artifactTypes: Array<ArtifactType> }) => {
    const existing = getAtom(taskUpdatedArtifactsAtomFamily(update.taskId));
    if (existing.length === 0) {
      return;
    }

    const artifactsToClear = new Set(update.artifactTypes);
    const remaining = existing.filter((artifactType) => !artifactsToClear.has(artifactType));

    if (remaining.length !== existing.length) {
      setAtom(taskUpdatedArtifactsAtomFamily(update.taskId), remaining);
    }
  },
);
