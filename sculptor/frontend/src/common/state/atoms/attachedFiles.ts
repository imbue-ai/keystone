import type { PrimitiveAtom } from "jotai";
import { atomFamily, atomWithStorage } from "jotai/utils";

import type { MessageID, ProjectID, TaskID } from "../../Types.ts";

export const attachedFilesAtomFamily = atomFamily<TaskID, PrimitiveAtom<Array<string>>>((taskId) => {
  return atomWithStorage<Array<string>>(`sculptor-attached-files-${taskId}`, []);
});

export const newTaskAttachedFilesAtomFamily = atomFamily<ProjectID, PrimitiveAtom<Array<string>>>((projectId) => {
  return atomWithStorage<Array<string>>(`sculptor-attached-files-${projectId}`, []);
});

export const forkAttachedFilesAtomFamily = atomFamily<MessageID | null, PrimitiveAtom<Array<string>>>((messageId) => {
  return atomWithStorage<Array<string>>(`sculptor-fork-attached-files-${messageId}`, []);
});
