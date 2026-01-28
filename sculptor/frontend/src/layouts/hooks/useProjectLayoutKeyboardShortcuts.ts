import { useAtomValue, useSetAtom } from "jotai";
import { useEffect } from "react";

import { useImbueLocation } from "../../common/NavigateUtils.ts";
import { matchesShortcutString } from "../../common/ShortcutUtils.ts";
import { isAttemptingToFocusTaskInputAtom, isSidebarOpenAtom } from "../../common/state/atoms/sidebar.ts";
import {
  newAgentShortcutAtom,
  searchAgentsShortcutAtom,
  toggleSidebarShortcutAtom,
} from "../../common/state/atoms/userConfig.ts";
import { useSearchModal } from "../../common/state/hooks/useSearchModal.ts";
import { useTaskModal } from "../../common/state/hooks/useTaskModal.ts";

export const useProjectLayoutKeyboardShortcuts = (): void => {
  const { toggleSearchModal, hideSearchModal, isSearchModalOpen } = useSearchModal();
  const { toggleTaskModal, hideTaskModal, isTaskModalOpen } = useTaskModal();
  const { isHomeRoute } = useImbueLocation();
  const setIsAttemptingToFocusTaskInput = useSetAtom(isAttemptingToFocusTaskInputAtom);
  const setIsSidebarOpen = useSetAtom(isSidebarOpenAtom);

  // Get configurable shortcuts from user settings
  const newAgentShortcut = useAtomValue(newAgentShortcutAtom);
  const searchAgentsShortcut = useAtomValue(searchAgentsShortcutAtom);
  const toggleSidebarShortcut = useAtomValue(toggleSidebarShortcutAtom);

  useEffect(() => {
    const toggleSidebar = (): void => {
      setIsSidebarOpen((prev) => !prev);
    };

    const handleKeyDown = (e: KeyboardEvent): void => {
      // Check for search agents shortcut
      if (searchAgentsShortcut && matchesShortcutString(e, searchAgentsShortcut)) {
        e.preventDefault();
        if (isTaskModalOpen) {
          hideTaskModal();
        }
        toggleSearchModal();
        return;
      }

      // Check for new agent shortcut
      if (newAgentShortcut && matchesShortcutString(e, newAgentShortcut)) {
        e.preventDefault();
        if (isSearchModalOpen) {
          hideSearchModal();
        }

        if (isHomeRoute) {
          setIsAttemptingToFocusTaskInput(true);
          return;
        }
        toggleTaskModal();
        return;
      }

      // Check for toggle sidebar shortcut
      if (toggleSidebarShortcut && matchesShortcutString(e, toggleSidebarShortcut)) {
        e.preventDefault();
        toggleSidebar();
        return;
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return (): void => window.removeEventListener("keydown", handleKeyDown);
  }, [
    hideSearchModal,
    hideTaskModal,
    isHomeRoute,
    isSearchModalOpen,
    isTaskModalOpen,
    setIsAttemptingToFocusTaskInput,
    toggleSearchModal,
    toggleTaskModal,
    newAgentShortcut,
    searchAgentsShortcut,
    setIsSidebarOpen,
    toggleSidebarShortcut,
  ]);
};
