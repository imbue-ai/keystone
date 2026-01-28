import { atom } from "jotai";

import type { MessageID } from "../../Types.ts";

export const taskModalOpenAtom = atom(false);

export const TaskModalMode = {
  CREATE_TASK: "CREATE_TASK",
  FORK_TASK: "FORK_TASK",
  EDIT_SYSTEM_PROMPT: "EDIT_SYSTEM_PROMPT",
} as const;

export type TaskModalMode = (typeof TaskModalMode)[keyof typeof TaskModalMode];

export const taskModalMessageIDAtom = atom<MessageID | null>(null);

export const taskModalModeAtom = atom<TaskModalMode>(TaskModalMode.CREATE_TASK);
