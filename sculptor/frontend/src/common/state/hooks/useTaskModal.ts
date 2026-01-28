import { useAtom, useSetAtom } from "jotai";

import { TaskModalMode, taskModalModeAtom, taskModalOpenAtom } from "../atoms/taskModal.ts";

type TaskModalControls = {
  isTaskModalOpen: boolean;
  setIsTaskModalOpen: (isOpen: boolean) => void;
  showTaskModal: () => void;
  hideTaskModal: () => void;
  toggleTaskModal: () => void;
};

export const useTaskModal = (): TaskModalControls => {
  const [isTaskModalOpen, setIsTaskModalOpen] = useAtom(taskModalOpenAtom);
  const setTaskModalMode = useSetAtom(taskModalModeAtom);

  const hideTaskModal = (): void => {
    setIsTaskModalOpen(false);
    setTaskModalMode(TaskModalMode.CREATE_TASK);
  };

  const showTaskModal = (): void => {
    setIsTaskModalOpen(true);
  };

  const toggleTaskModal = (): void => {
    setTaskModalMode(TaskModalMode.CREATE_TASK);
    setIsTaskModalOpen((prev) => !prev);
  };

  return {
    isTaskModalOpen,
    setIsTaskModalOpen,
    showTaskModal,
    hideTaskModal,
    toggleTaskModal,
  };
};
