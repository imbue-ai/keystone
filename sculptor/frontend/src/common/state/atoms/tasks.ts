import type { PrimitiveAtom } from "jotai";
import { atom } from "jotai";
import { atomFamily } from "jotai/utils";

import type { CodingAgentTaskView } from "../../../api";

export const taskAtomFamily = atomFamily<string, PrimitiveAtom<CodingAgentTaskView | null>>(() =>
  atom<CodingAgentTaskView | null>(null),
);

export const taskIdsAtom = atom<ReadonlyArray<string> | undefined>(undefined);

export const tasksArrayAtom = atom<ReadonlyArray<CodingAgentTaskView> | undefined>((get) => {
  const taskIds = get(taskIdsAtom);
  if (taskIds === undefined) {
    return undefined;
  }
  return taskIds
    .map((id) => get(taskAtomFamily(id)))
    .filter((task): task is CodingAgentTaskView => task !== null && !task.isDeleted);
});

export const updateTasksAtom = atom(null, (get, set, updates: Record<string, CodingAgentTaskView>) => {
  const seenIds = new Set(get(taskIdsAtom));

  Object.entries(updates).forEach(([id, task]) => {
    set(taskAtomFamily(id), task);
    seenIds.add(id);

    if (task.isDeleted) {
      seenIds.delete(id);
      set(taskAtomFamily(id), null);
      return;
    }
  });

  set(taskIdsAtom, Array.from(seenIds));
});

export const navigateToMostRecentSuggestionsTurnAtomFamily = atomFamily<string, PrimitiveAtom<number>>(() =>
  atom<number>(0),
);

export const selectedArtifactIdAtomFamily = atomFamily<string, PrimitiveAtom<string>>(() => atom<string>(""));

export const isNarrowViewingChatAtomFamily = atomFamily<string, PrimitiveAtom<boolean>>(() => atom<boolean>(true));

export const flashTabIdAtomFamily = atomFamily<string, PrimitiveAtom<string | null>>(() => atom<string | null>(null));

export const chatScrollPositionAtomFamily = atomFamily<string, PrimitiveAtom<number | null>>(() =>
  atom<number | null>(null),
);
