import { useAtom } from "jotai";

import type { MessageID, ProjectID, TaskID } from "../../Types.ts";
import {
  attachedFilesAtomFamily,
  forkAttachedFilesAtomFamily,
  newTaskAttachedFilesAtomFamily,
} from "../atoms/attachedFiles.ts";

// TODO: the return type for this is complicated and not everything we want is exported in Jotai.
// eslint-disable-next-line @typescript-eslint/explicit-function-return-type
export const useDraftAttachedFiles = (taskId: TaskID) => {
  return useAtom(attachedFilesAtomFamily(taskId));
};

// eslint-disable-next-line @typescript-eslint/explicit-function-return-type
export const useNewTaskDraftAttachedFiles = (projectId: ProjectID) => {
  return useAtom(newTaskAttachedFilesAtomFamily(projectId));
};

// eslint-disable-next-line @typescript-eslint/explicit-function-return-type
export const useForkDraftAttachedFiles = (messageId: MessageID | null) => {
  return useAtom(forkAttachedFilesAtomFamily(messageId));
};
