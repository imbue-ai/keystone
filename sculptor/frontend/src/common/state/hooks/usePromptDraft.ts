import { useAtom } from "jotai";

import type { MessageID, ProjectID, TaskID } from "../../Types.ts";
import { forkPromptDraftAtomFamily, newTaskPromptDraftAtomFamily, promptDraftAtomFamily } from "../atoms/promptDrafts";

export const usePromptDraft = (taskId: TaskID): [string | null, (value: string | null) => void] => {
  return useAtom(promptDraftAtomFamily(taskId));
};

export const useNewTaskPromptDraft = (projectId: ProjectID): [string | null, (value: string | null) => void] => {
  return useAtom(newTaskPromptDraftAtomFamily(projectId));
};

export const useForkPromptDraft = (messageId: MessageID | null): [string | null, (value: string | null) => void] => {
  return useAtom(forkPromptDraftAtomFamily(messageId));
};
