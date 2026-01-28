import { Badge, Dialog, Flex, IconButton, SegmentedControl, Text } from "@radix-ui/themes";
import { useAtom } from "jotai";
import { HomeIcon, PanelLeftIcon, PanelRightIcon, SplitIcon, X } from "lucide-react";
import type { ReactElement } from "react";
import { useCallback, useMemo, useState } from "react";

import type { DiffArtifact } from "../../../api";
import { ElementIds } from "../../../api";
import { useImbueNavigate, useTaskPageParams } from "../../../common/NavigateUtils.ts";
import { isReviewModalOpenAtom } from "../../../common/state/atoms/modals.ts";
import { isRightPanelOpenAtom, isSidebarOpenAtom } from "../../../common/state/atoms/sidebar.ts";
import { useIsNarrowLayout } from "../../../common/state/hooks/useComponentWidthById.ts";
import { useTask } from "../../../common/state/hooks/useTaskHelpers.ts";
import { mergeClasses } from "../../../common/Utils.ts";
import { MultiFileDiffView } from "../../../components/MultiFileDiff.tsx";
import { Toast, type ToastContent } from "../../../components/Toast.tsx";
import { TooltipIconButton } from "../../../components/TooltipIconButton.tsx";
import { getMetaKey, getTitleBarLeftPadding } from "../../../electron/utils.ts";
import { HeaderSkeleton, HeaderUnbuilt } from "../../../pages/chat/components/HeaderSkeletons.tsx";
import { TaskActionsMenu } from "../../home/components/TaskActionsMenu.tsx";
import { getBranchName } from "../../home/Utils.ts";
import styles from "./Header.module.scss";
import { SyncButton } from "./SyncButton.tsx";
import { SyncPanel } from "./SyncPanel.tsx";

type ChatHeaderProps = {
  diffArtifact: DiffArtifact | undefined;
};

export const Header = ({ diffArtifact }: ChatHeaderProps): ReactElement => {
  const { projectID, taskID } = useTaskPageParams();

  const task = useTask(taskID);
  const [isReviewModalOpen, setIsReviewModalOpen] = useAtom(isReviewModalOpenAtom);
  const { navigateToHome, navigateToChat } = useImbueNavigate();
  const [syncToast, setSyncToast] = useState<ToastContent | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isArchiving, setIsArchiving] = useState(false);
  const [isSideBarOpen, setIsSideBarOpen] = useAtom(isSidebarOpenAtom);
  const [isRightPanelOpen, setIsRightPanelOpen] = useAtom(isRightPanelOpenAtom);
  const isNarrowLayout = useIsNarrowLayout();

  const handleNavigateToParent = useCallback(() => {
    if (task?.parentId) {
      navigateToChat(projectID, task.parentId);
    }
  }, [navigateToChat, projectID, task?.parentId]);

  const parentTask = useTask(task?.parentId || "");

  const handleCopyBranch = (): void => {
    if (!task) {
      return;
    }

    const branchName = getBranchName(task);
    if (!branchName) {
      return;
    }
    navigator.clipboard.writeText(branchName);
  };

  const [selectedTab, setSelectedTab] = useState<"complete" | "branch" | "uncommitted">("complete");

  const handleValueChange = useCallback(
    (value: string) => {
      setSelectedTab(value as "complete" | "branch" | "uncommitted");
    },
    [setSelectedTab],
  );

  const currentDiffData = useMemo(() => {
    if (!diffArtifact) return null;
    switch (selectedTab) {
      case "complete":
        return diffArtifact.completeDiff;
      case "branch":
        return diffArtifact.committedDiff;
      case "uncommitted":
        return diffArtifact.uncommittedDiff;
      default:
        return diffArtifact.completeDiff;
    }
  }, [selectedTab, diffArtifact]);

  const handleNavigateHome = (): void => {
    navigateToHome(projectID);
  };

  if (!task) {
    return <HeaderSkeleton />;
  }

  if (!getBranchName(task)) {
    return <HeaderUnbuilt projectId={projectID} task={task} />;
  }

  const leftPadding = getTitleBarLeftPadding(isSideBarOpen);
  const metaKey = getMetaKey();

  return (
    <>
      <Flex
        align="center"
        gap="3"
        py="2"
        pr="3"
        pl={leftPadding}
        justify="between"
        className={styles.headerContainer}
        data-testid={ElementIds.TASK_HEADER}
        overflow="hidden"
      >
        <Flex align="center" gap="3" minWidth="0" flexGrow="1" className={styles.left}>
          {!isSideBarOpen && (
            <>
              <TooltipIconButton
                tooltipText={`Toggle sidebar (${metaKey}B)`}
                variant="ghost"
                onClick={() => setIsSideBarOpen((prev) => !prev)}
                data-state={isSideBarOpen ? "open" : "closed"}
                data-testid={ElementIds.TOGGLE_SIDEBAR_BUTTON}
                aria-label="Toggle sidebar"
                className={mergeClasses(styles.nonDraggable, styles.fixed)}
              >
                <PanelLeftIcon width={16} height={16} />
              </TooltipIconButton>
              <TooltipIconButton
                tooltipText="Go to project home"
                variant="ghost"
                onClick={handleNavigateHome}
                aria-label="Go to project home"
                className={mergeClasses(styles.homeButton, styles.nonDraggable, styles.fixed)}
                data-testid={ElementIds.HOME_BUTTON}
              >
                <HomeIcon />
              </TooltipIconButton>
            </>
          )}
          {task.parentId !== null && (
            <TooltipIconButton
              tooltipText={`Forked from ${parentTask?.branchName}`}
              onClick={handleNavigateToParent}
              className={styles.forkIcon}
            >
              <SplitIcon />
            </TooltipIconButton>
          )}
          <Text truncate className={mergeClasses(styles.titleText, styles.shrinkP1)}>
            {task.title}
          </Text>
          <div className={mergeClasses(styles.branchBadgeDiv, styles.shrinkP2)}>
            <Badge
              color="gold"
              className={styles.branchBadge}
              onClick={handleCopyBranch}
              data-testid={ElementIds.BRANCH_NAME}
              data-source-branch={task.sourceBranch}
            >
              {task.branchName}
            </Badge>
          </div>
          <TaskActionsMenu
            projectId={projectID}
            task={task}
            isDeleting={isDeleting}
            setIsDeleting={setIsDeleting}
            isArchiving={isArchiving}
            setIsArchiving={setIsArchiving}
          />
        </Flex>
        <Flex gap="3" align="center" flexShrink="0">
          <Flex gapX="2">
            <SyncPanel
              task={task}
              projectID={projectID}
              toastCallback={setSyncToast}
              disabled={isDeleting || isArchiving}
            />
            <SyncButton
              task={task}
              currentProjectID={projectID}
              toastCallback={setSyncToast}
              disabled={isDeleting || isArchiving}
              widgetStyle="REGULAR"
              buttonContext="PRIMARY"
            />
          </Flex>
          {!isNarrowLayout && (
            <TooltipIconButton
              tooltipText="Toggle right panel"
              variant="ghost"
              onClick={() => setIsRightPanelOpen((prev) => !prev)}
              data-state={isRightPanelOpen ? "open" : "closed"}
              aria-label="Toggle right panel"
            >
              <PanelRightIcon width={16} height={16} />
            </TooltipIconButton>
          )}
        </Flex>
      </Flex>
      {/*TODO: move this dialogue from outside of the header and use a custom hook to open and close it instead, with the modal component near the DOM root*/}
      <Dialog.Root open={isReviewModalOpen} onOpenChange={setIsReviewModalOpen}>
        <Dialog.Content className={styles.diffReviewModal}>
          <Flex direction="column" height="100%" width="100%" className={styles.dialogContent} pl="4">
            <SegmentedControl.Root
              value={selectedTab}
              onValueChange={handleValueChange}
              size="1"
              className={styles.reviewTabBar}
            >
              <SegmentedControl.Item value="complete">All</SegmentedControl.Item>
              <SegmentedControl.Item value="branch">Committed</SegmentedControl.Item>
              <SegmentedControl.Item value="uncommitted">Uncommitted</SegmentedControl.Item>
            </SegmentedControl.Root>
            <Flex direction="column" flexGrow="1" width="100%" overflow="hidden">
              {currentDiffData === null ? (
                <Text>Changes loading...</Text>
              ) : (
                <MultiFileDiffView multiFileDiffString={currentDiffData || ""} isDefaultFileTreeVisible={true} />
              )}
            </Flex>
            <Flex justify="end" align="start" style={{ position: "absolute", right: "30px" }}>
              <Dialog.Close>
                <IconButton variant="ghost" size="1">
                  <X size={18} />
                </IconButton>
              </Dialog.Close>
            </Flex>
          </Flex>
        </Dialog.Content>
      </Dialog.Root>
      <Toast open={!!syncToast} onOpenChange={(open) => !open && setSyncToast(null)} {...(syncToast || {})} />
    </>
  );
};
