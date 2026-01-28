import * as Dialog from "@radix-ui/react-dialog";
import { Cross1Icon } from "@radix-ui/react-icons";
import { Badge, Box, Flex, IconButton, Text, TextField, VisuallyHidden } from "@radix-ui/themes";
import { useAtom } from "jotai";
import { SearchIcon } from "lucide-react";
import type { ReactElement } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useMemo } from "react";

import { ElementIds } from "../api";
import { useImbueNavigate, useProjectPageParams } from "../common/NavigateUtils.ts";
import { searchModalContentsAtom } from "../common/state/atoms/searchModal.ts";
import { useSearchModal } from "../common/state/hooks/useSearchModal.ts";
import { useTasks } from "../common/state/hooks/useTaskHelpers.ts";
import { mergeClasses, optional } from "../common/Utils.ts";
import styles from "./SearchModal.module.scss";

export const SearchModal = (): ReactElement => {
  const { isSearchModalOpen, hideSearchModal } = useSearchModal();
  const { projectID } = useProjectPageParams();
  const [searchValue, setSearchValue] = useAtom(searchModalContentsAtom);
  const { tasks: allTasks } = useTasks(projectID);
  const [selectedIndex, setSelectedIndex] = useState(0);
  // TODO: add archived tasks toggle once designed
  // eslint-disable-next-line no-unused-vars, @typescript-eslint/no-unused-vars
  const [isShowingArchived, setIsShowingArchived] = useState(false);
  const [windowStart, setWindowStart] = useState(0);
  const [isUsingKeyboard, setIsUsingKeyboard] = useState(false);
  const { navigateToChat } = useImbueNavigate();
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  const VISIBLE_ITEMS = 5;
  const filteredTasks = useMemo(() => {
    const sortedTasks = [...allTasks].sort((a, b) => {
      const aTime = new Date(a.createdAt).getTime();
      const bTime = new Date(b.createdAt).getTime();
      return bTime - aTime;
    });

    return sortedTasks.filter((task) => {
      const isArchiveFilterMatch = isShowingArchived ? task.isArchived : !task.isArchived;
      const isSearchFilterMatch =
        searchValue === "" ||
        task.initialPrompt?.toLowerCase().includes(searchValue.toLowerCase()) ||
        (task.title && task.title.toLowerCase().includes(searchValue.toLowerCase()));

      return isArchiveFilterMatch && isSearchFilterMatch;
    });
  }, [allTasks, isShowingArchived, searchValue]);

  const visibleTasks = useMemo(() => {
    return filteredTasks.slice(windowStart, windowStart + VISIBLE_ITEMS);
  }, [filteredTasks, windowStart]);

  useEffect(() => {
    setSelectedIndex(0);
    setWindowStart(0);
  }, [searchValue, isShowingArchived]);

  useEffect(() => {
    if (selectedIndex >= visibleTasks.length && visibleTasks.length > 0) {
      setSelectedIndex(visibleTasks.length - 1);
    }
  }, [visibleTasks, selectedIndex]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      switch (e.key) {
        case "ArrowUp":
          e.preventDefault();
          setIsUsingKeyboard(true);
          if (selectedIndex > 0) {
            setSelectedIndex(selectedIndex - 1);
          } else if (windowStart > 0) {
            setWindowStart(windowStart - 1);
          }
          break;
        case "ArrowDown":
          e.preventDefault();
          setIsUsingKeyboard(true);
          if (selectedIndex < visibleTasks.length - 1) {
            setSelectedIndex(selectedIndex + 1);
          } else if (windowStart + VISIBLE_ITEMS < filteredTasks.length) {
            setWindowStart(windowStart + 1);
          }
          break;
        case "Enter":
          e.preventDefault();
          if (visibleTasks[selectedIndex]) {
            navigateToChat(projectID, visibleTasks[selectedIndex].id);
            hideSearchModal();
            setSearchValue("");
          }
          break;
        case "Escape":
          e.preventDefault();
          hideSearchModal();
          break;
      }
    },
    [
      selectedIndex,
      windowStart,
      visibleTasks,
      filteredTasks.length,
      hideSearchModal,
      navigateToChat,
      projectID,
      setSearchValue,
    ],
  );

  const handleWheel = useCallback(
    (e: React.WheelEvent) => {
      e.preventDefault();
      if (e.deltaY > 0) {
        if (windowStart + VISIBLE_ITEMS < filteredTasks.length) {
          setWindowStart(windowStart + 1);
        }
      } else if (e.deltaY < 0) {
        if (windowStart > 0) {
          setWindowStart(windowStart - 1);
        }
      }
    },
    [windowStart, filteredTasks.length],
  );

  return (
    <>
      <Dialog.Root
        open={isSearchModalOpen}
        onOpenChange={(o) => {
          if (!o) {
            hideSearchModal();
          }
        }}
      >
        <VisuallyHidden>
          <Dialog.Title>Search agents</Dialog.Title>
        </VisuallyHidden>
        <Dialog.Content
          className={styles.modalContainer}
          onKeyDown={handleKeyDown}
          data-testid={ElementIds.SEARCH_MODAL}
        >
          <Flex direction="column" className={styles.body}>
            <Box className={styles.panel} tabIndex={-1}>
              <Box position="absolute" top="22px" right="4">
                <Dialog.Close asChild>
                  <IconButton
                    variant="ghost"
                    size="1"
                    aria-label="Close"
                    data-no-panel-focus
                    data-testid={ElementIds.SEARCH_MODAL_CLOSE_BUTTON}
                  >
                    <Cross1Icon />
                  </IconButton>
                </Dialog.Close>
              </Box>
              {/* search field */}
              <Box className={styles.panelBody} py="3" px="3">
                <TextField.Root
                  value={searchValue || ""}
                  placeholder="Search for agent..."
                  onChange={(e) => setSearchValue(e.target.value)}
                  className={styles.searchField}
                  onKeyDown={handleKeyDown}
                  autoFocus
                  variant="surface"
                  data-testid={ElementIds.SEARCH_MODAL_INPUT}
                >
                  <TextField.Slot>
                    <SearchIcon />
                  </TextField.Slot>
                </TextField.Root>
              </Box>
              {/* filtered tasks section */}
              <Flex
                direction="column"
                px="4"
                py="3"
                ref={scrollContainerRef}
                onWheel={handleWheel}
                overflowY="hidden"
                data-testid={ElementIds.SEARCH_MODAL_TASK_LIST}
              >
                {visibleTasks.map((task, index) => (
                  <Flex
                    key={task.id}
                    justify="between"
                    p="3"
                    className={mergeClasses(styles.taskItem, optional(selectedIndex === index, styles.selectedTask))}
                    onClick={() => {
                      navigateToChat(projectID, task.id);
                      hideSearchModal();
                      setSearchValue("");
                    }}
                    onMouseEnter={() => {
                      if (!isUsingKeyboard) {
                        setSelectedIndex(index);
                      }
                    }}
                    onMouseMove={() => {
                      setIsUsingKeyboard(false);
                      setSelectedIndex(index);
                    }}
                    data-testid={ElementIds.SEARCH_MODAL_TASK_ITEM}
                    data-is-selected={selectedIndex === index}
                  >
                    <Text>{task.title || task.initialPrompt}</Text>
                    <Badge className={styles.branchBadge}>{task.branchName || "untitled branch"}</Badge>
                  </Flex>
                ))}
                {filteredTasks.length === 0 && (
                  <Flex
                    className={styles.noTasks}
                    justify="center"
                    align="center"
                    p="3"
                    data-testid={ElementIds.SEARCH_MODAL_NO_TASKS}
                  >
                    <Text className={styles.noTasksFound}>No tasks found</Text>
                  </Flex>
                )}
              </Flex>
              {/* footer with keyboard shortcuts */}
              <Flex justify="between" align="center" gapX="3" py="3" px="4" className={styles.footer}>
                <Flex gapX="3">
                  <Text size="1" color="gray">
                    <kbd>↑↓</kbd> to navigate
                  </Text>
                  <Text size="1" color="gray">
                    <kbd>⏎</kbd> to open
                  </Text>
                  <Text size="1" color="gray">
                    <kbd>Esc</kbd> to close
                  </Text>
                </Flex>
              </Flex>
            </Box>
          </Flex>
        </Dialog.Content>
      </Dialog.Root>
    </>
  );
};
