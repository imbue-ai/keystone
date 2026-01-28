import { useAtomValue } from "jotai";

import type { CodingAgentTaskView } from "../../../api";
import { taskAtomFamily, tasksArrayAtom } from "../atoms/tasks";

class ExpectedObjectNotFoundError extends Error {}

export const useTask = (taskId: string): CodingAgentTaskView | null => {
  return useAtomValue(taskAtomFamily(taskId));
};

export const useStrictTask = (taskId: string): CodingAgentTaskView => {
  const maybeTask = useTask(taskId);

  if (!maybeTask) {
    throw new ExpectedObjectNotFoundError(`Expected task (${taskId}) to exist but it did not`);
  }

  return maybeTask;
};

export type UseTasksReturn = {
  tasks: ReadonlyArray<CodingAgentTaskView>;
  isLoading: boolean;
};

export const useTasks = (projectId: string): UseTasksReturn => {
  const tasks = useAtomValue(tasksArrayAtom);
  if (tasks === undefined) {
    return { tasks: [], isLoading: true };
  }
  return {
    tasks: tasks.filter((task) => task.projectId === projectId),
    isLoading: false,
  };
};
