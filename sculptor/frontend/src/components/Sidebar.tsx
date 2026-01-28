import { ArrowLeftIcon } from "@radix-ui/react-icons";
import { Box, Button, Flex, ScrollArea, Text } from "@radix-ui/themes";
import { useAtom, useAtomValue, useSetAtom } from "jotai";
import { ArchiveIcon, HomeIcon, PanelLeftIcon, PlusIcon, SearchIcon, SettingsIcon } from "lucide-react";
import { type ReactElement, useMemo, useState } from "react";

import { ElementIds } from "../api";
import { useImbueLocation, useImbueNavigate, useProjectPageParams } from "../common/NavigateUtils.ts";
import { formatShortcutForDisplay } from "../common/ShortcutUtils.ts";
import { isAttemptingToFocusTaskInputAtom, isSidebarOpenAtom } from "../common/state/atoms/sidebar.ts";
import {
  newAgentShortcutAtom,
  searchAgentsShortcutAtom,
  toggleSidebarShortcutAtom,
} from "../common/state/atoms/userConfig.ts";
import { useSearchModal } from "../common/state/hooks/useSearchModal.ts";
import { useTasks } from "../common/state/hooks/useTaskHelpers.ts";
import { useTaskModal } from "../common/state/hooks/useTaskModal.ts";
import { mergeClasses, optional } from "../common/Utils.ts";
import { getTitleBarLeftPadding, TITLEBAR_HEIGHT } from "../electron/utils.ts";
import { HelpMenu } from "./HelpMenu.tsx";
import { ProjectSelector } from "./ProjectSelector.tsx";
import styles from "./Sidebar.module.scss";
import { TaskItem } from "./TaskItem.tsx";
import { TaskListSkeleton } from "./TaskItemSkeleton.tsx";
import { Toast, type ToastContent } from "./Toast.tsx";
import { TooltipIconButton } from "./TooltipIconButton.tsx";

export const Sidebar = (): ReactElement => {
  const { projectID } = useProjectPageParams();
  const { isHomeRoute } = useImbueLocation();
  const { showTaskModal } = useTaskModal();
  const { navigateToHome, navigateToSettings } = useImbueNavigate();
  const setIsAttemptingToFocusTaskInput = useSetAtom(isAttemptingToFocusTaskInputAtom);
  const [isShowingArchived, setIsShowingArchived] = useState(false);
  const [toast, setToast] = useState<ToastContent | null>(null);
  const { tasks: allTasks, isLoading: areTasksLoading } = useTasks(projectID);
  const [isSideBarOpen, setIsSideBarOpen] = useAtom(isSidebarOpenAtom);
  const { showSearchModal } = useSearchModal();

  // Get configurable shortcuts from user settings
  const newAgentShortcut = useAtomValue(newAgentShortcutAtom);
  const searchAgentsShortcut = useAtomValue(searchAgentsShortcutAtom);
  const toggleSidebarShortcut = useAtomValue(toggleSidebarShortcutAtom);

  const filteredTasks = useMemo(() => {
    const sortedTasks = [...allTasks].sort((a, b) => {
      const aTime = new Date(a.createdAt).getTime();
      const bTime = new Date(b.createdAt).getTime();
      return bTime - aTime;
    });

    return sortedTasks.filter((task) => {
      return isShowingArchived ? task.isArchived : !task.isArchived;
    });
  }, [allTasks, isShowingArchived]);

  const handleNavigateHome = (): void => {
    navigateToHome(projectID);
  };

  const handleClickNewAgent = (): void => {
    if (isHomeRoute) {
      // Focus the prompt input if we're already on the home route
      setIsAttemptingToFocusTaskInput(true);
      return;
    }
    showTaskModal();
  };

  const archiveButtonTooltip = isShowingArchived ? "Show Active Agents" : "Show Archived Agents";
  return (
    <Flex
      direction="column"
      justify="between"
      pb="3"
      className={styles.container}
      height="100%"
      data-testid={ElementIds.SIDEBAR}
    >
      <Flex direction="column" flexGrow="1" overflow="hidden">
        {/* Titlebar row */}
        {/* NOTE: I have no idea why I need to add 4px, but without it, it gets slightly vertically misaligned from the sidebar closed buttons*/}
        <Flex
          justify="between"
          align="center"
          pl={getTitleBarLeftPadding(false)}
          pr="10px"
          height={`${TITLEBAR_HEIGHT + 4}px`}
          className={styles.draggable}
          flexShrink="0"
        >
          <Flex>
            <TooltipIconButton
              tooltipText={`Toggle sidebar (${formatShortcutForDisplay(toggleSidebarShortcut)})`}
              variant="ghost"
              onClick={() => setIsSideBarOpen((prev) => !prev)}
              data-state={isSideBarOpen ? "open" : "closed"}
              data-testid={ElementIds.TOGGLE_SIDEBAR_BUTTON}
              className={styles.nonDraggable}
            >
              <PanelLeftIcon width={16} height={16} />
            </TooltipIconButton>
          </Flex>
          <Flex>
            <TooltipIconButton
              tooltipText={`Search for agents (${formatShortcutForDisplay(searchAgentsShortcut)})`}
              variant="ghost"
              onClick={showSearchModal}
              aria-label="Search for agents"
              className={mergeClasses(styles.homeButton, styles.nonDraggable)}
              data-testid={ElementIds.SEARCH_MODAL_OPEN_BUTTON}
            >
              <SearchIcon />
            </TooltipIconButton>
            <TooltipIconButton
              tooltipText="Go to project home"
              variant="ghost"
              onClick={handleNavigateHome}
              aria-label="Go to project home"
              className={mergeClasses(styles.homeButton, styles.nonDraggable)}
              data-testid={ElementIds.HOME_BUTTON}
            >
              <HomeIcon />
            </TooltipIconButton>
          </Flex>
        </Flex>
        {/* Top controls row */}
        <Flex align="center" width="100%" gapX="3" mb="3" px="4">
          {isShowingArchived ? (
            <Box width="100%">
              <Button
                onClick={() => setIsShowingArchived(false)}
                className={styles.backButton}
                data-testid={ElementIds.BACK_TO_ACTIVE_AGENTS_BUTTON}
              >
                <ArrowLeftIcon />
                <Text>Back to active agents</Text>
              </Button>
            </Box>
          ) : (
            <>
              <Box flexGrow="1" px="9px" mt="2">
                <Button
                  className={styles.newAgentButton}
                  onClick={handleClickNewAgent}
                  variant="ghost"
                  data-testid={ElementIds.NEW_AGENT_BUTTON}
                >
                  <Flex justify="between" align="center" width="100%">
                    <Flex align="center" gapX="2">
                      <PlusIcon />
                      <Text>New Agent</Text>
                    </Flex>
                    <Text>{formatShortcutForDisplay(newAgentShortcut)}</Text>
                  </Flex>
                </Button>
              </Box>
            </>
          )}
        </Flex>
        {/* Task list*/}
        {!areTasksLoading && (
          <ScrollArea className={styles.scrollArea}>
            <Flex direction="column" px="4" data-testid={ElementIds.TASK_LIST}>
              {filteredTasks.length === 0 && (
                <Flex className={styles.noTasks} justify="center" align="center" p="3">
                  <Text color="gray">{isShowingArchived ? "No archived agents yet..." : "No agents yet..."}</Text>
                </Flex>
              )}
              {filteredTasks.map((task) => (
                <TaskItem projectId={projectID} task={task} key={task.id} />
              ))}
            </Flex>
          </ScrollArea>
        )}
        {areTasksLoading && <TaskListSkeleton />}
      </Flex>
      <Flex direction="column" gapY="3" px="4" pt="3">
        {/* Bottom controls row */}
        <Flex direction="row" justify="between" align="center" px="1">
          <TooltipIconButton
            tooltipText={archiveButtonTooltip}
            variant="ghost"
            onClick={() => setIsShowingArchived(!isShowingArchived)}
            className={optional(isShowingArchived, styles.archiveButtonArchived)}
            data-testid={ElementIds.VIEW_ARCHIVED_TASKS_BUTTON}
          >
            <ArchiveIcon />
          </TooltipIconButton>
          <Flex gapX="3">
            <TooltipIconButton
              tooltipText="Go to settings page"
              onClick={() => navigateToSettings(projectID)}
              variant="ghost"
              data-testid={ElementIds.SETTINGS_BUTTON}
            >
              <SettingsIcon />
            </TooltipIconButton>
            <HelpMenu />
          </Flex>
        </Flex>
        <ProjectSelector />
      </Flex>
      <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} title={toast?.title} type={toast?.type} />
    </Flex>
  );
};
