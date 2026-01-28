import type { PrimitiveAtom } from "jotai";
import { atomFamily, atomWithStorage } from "jotai/utils";

import type { MessageID, ProjectID, TaskID } from "../../Types.ts";

export const promptDraftAtomFamily = atomFamily<TaskID, PrimitiveAtom<string | null>>((taskId) => {
  return atomWithStorage<string | null>(`sculptor-prompt-draft-${taskId}`, null);
});

export const newTaskPromptDraftAtomFamily = atomFamily<ProjectID, PrimitiveAtom<string | null>>((projectId) => {
  return atomWithStorage<string | null>(`sculptor-prompt-draft-${projectId}`, null);
});

export const forkPromptDraftAtomFamily = atomFamily<MessageID | null, PrimitiveAtom<string | null>>((messageId) => {
  return atomWithStorage<string | null>(`sculptor-fork-prompt-draft-${messageId}`, null);
});
