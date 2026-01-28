import { Badge, Button, Flex, Skeleton, Text } from "@radix-ui/themes";
import { useAtom } from "jotai";
import { ArrowUpDownIcon, ChevronDownIcon, HomeIcon, PanelLeftIcon, PanelRightIcon } from "lucide-react";
import type { ReactElement } from "react";
import { useState } from "react";

import { type CodingAgentTaskView, ElementIds } from "../../../api";
import { useImbueNavigate, useTaskPageParams } from "../../../common/NavigateUtils.ts";
import { isRightPanelOpenAtom, isSidebarOpenAtom } from "../../../common/state/atoms/sidebar.ts";
import { mergeClasses } from "../../../common/Utils.ts";
import { TooltipIconButton } from "../../../components/TooltipIconButton.tsx";
import { getMetaKey, getTitleBarLeftPadding } from "../../../electron/utils.ts";
import { TaskActionsMenu } from "../../home/components/TaskActionsMenu.tsx";
import styles from "./Header.module.scss";

export const HeaderSkeleton = (): ReactElement => {
  const [isSideBarOpen, setIsSideBarOpen] = useAtom(isSidebarOpenAtom);
  const { projectID } = useTaskPageParams();
  const [isRightPanelOpen, setIsRightPanelOpen] = useAtom(isRightPanelOpenAtom);

  const { navigateToHome } = useImbueNavigate();
  const handleNavigateHome = (): void => {
    navigateToHome(projectID);
  };

  const leftPadding = getTitleBarLeftPadding(isSideBarOpen);
  const metaKey = getMetaKey();

  return (
    <>
      <Flex
        align="center"
        gap="3"
        p="2"
        pl={leftPadding}
        justify="between"
        className={styles.headerContainer}
        data-testid={ElementIds.TASK_HEADER}
      >
        <Flex align="center" gap="3">
          {!isSideBarOpen && (
            <>
              <TooltipIconButton
                tooltipText={`Toggle sidebar (${metaKey}B)`}
                variant="ghost"
                onClick={() => setIsSideBarOpen((prev) => !prev)}
                data-state={isSideBarOpen ? "open" : "closed"}
                data-testid={ElementIds.TOGGLE_SIDEBAR_BUTTON}
                aria-label="Toggle sidebar"
                className={styles.nonDraggable}
              >
                <PanelLeftIcon width={16} height={16} />
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
            </>
          )}
          <Skeleton>
            <Text className={styles.titleText}>Hello this is a fake task name hehe</Text>
          </Skeleton>
          <Skeleton>
            <Text className={styles.titleText}>sculptor/fake-branch-name</Text>
          </Skeleton>
        </Flex>
        <Flex gapX="3" mr="2" align="center">
          <Button size="1" disabled={true}>
            <Flex gapX="1" align="center">
              <Text>Merge</Text>
              <ChevronDownIcon />
            </Flex>
          </Button>
          <Button size="1" disabled={true}>
            <ArrowUpDownIcon />
            <Text>Pairing Mode</Text>
          </Button>
          <TooltipIconButton
            tooltipText="Toggle right panel"
            variant="ghost"
            onClick={() => setIsRightPanelOpen((prev) => !prev)}
            data-state={isRightPanelOpen ? "open" : "closed"}
            aria-label="Toggle right panel"
          >
            <PanelRightIcon width={16} height={16} />
          </TooltipIconButton>
        </Flex>
      </Flex>
    </>
  );
};

type HeaderUnbuiltProps = {
  projectId: string;
  task: CodingAgentTaskView;
};
export const HeaderUnbuilt = ({ projectId, task }: HeaderUnbuiltProps): ReactElement => {
  const [isDeleting, setIsDeleting] = useState(false);
  const [isArchiving, setIsArchiving] = useState(false);
  const [isRightPanelOpen, setIsRightPanelOpen] = useAtom(isRightPanelOpenAtom);

  const [isSideBarOpen, setIsSideBarOpen] = useAtom(isSidebarOpenAtom);
  const { projectID } = useTaskPageParams();

  const { navigateToHome } = useImbueNavigate();
  const handleNavigateHome = (): void => {
    navigateToHome(projectID);
  };

  const leftPadding = getTitleBarLeftPadding(isSideBarOpen);
  const metaKey = getMetaKey();

  return (
    <>
      <Flex
        align="center"
        gap="3"
        p="2"
        pl={leftPadding}
        justify="between"
        className={styles.headerContainer}
        data-testid={ElementIds.TASK_HEADER}
      >
        <Flex align="center" gap="3">
          {!isSideBarOpen && (
            <>
              <TooltipIconButton
                tooltipText={`Toggle sidebar (${metaKey}B)`}
                variant="ghost"
                onClick={() => setIsSideBarOpen((prev) => !prev)}
                data-state={isSideBarOpen ? "open" : "closed"}
                data-testid={ElementIds.TOGGLE_SIDEBAR_BUTTON}
                aria-label="Toggle sidebar"
                className={styles.nonDraggable}
              >
                <PanelLeftIcon width={16} height={16} />
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
            </>
          )}
          <Text className={styles.titleTextBuilding}>Untitled task</Text>
          <Flex style={{ position: "relative" }}>
            <Badge className={styles.branchBadgeBuilding} data-testid={ElementIds.BRANCH_NAME} data-is-skeleton="true">
              generating branch
            </Badge>
          </Flex>
          <TaskActionsMenu
            projectId={projectId}
            task={task}
            isDeleting={isDeleting}
            setIsDeleting={setIsDeleting}
            isArchiving={isArchiving}
            setIsArchiving={setIsArchiving}
          />
        </Flex>
        <Flex gapX="3" mr="2" align="center">
          <Button size="1" disabled={true}>
            <Flex gapX="1" align="center">
              <Text>Merge</Text>
              <ChevronDownIcon />
            </Flex>
          </Button>
          <Button size="1" disabled={true}>
            <ArrowUpDownIcon />
            <Text>Pairing Mode</Text>
          </Button>
          <TooltipIconButton
            tooltipText="Toggle right panel"
            variant="ghost"
            onClick={() => setIsRightPanelOpen((prev) => !prev)}
            data-state={isRightPanelOpen ? "open" : "closed"}
            aria-label="Toggle right panel"
          >
            <PanelRightIcon width={16} height={16} />
          </TooltipIconButton>
        </Flex>
      </Flex>
    </>
  );
};
