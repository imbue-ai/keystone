import { Flex, Text, Tooltip } from "@radix-ui/themes";
import type { ReactElement } from "react";
import { useCallback, useEffect, useState } from "react";

import type { CodingAgentTaskView } from "../api";
import { ElementIds, LocalSyncStatus } from "../api";
import { TaskStatus } from "../api";
import { useImbueNavigate, useImbueParams } from "../common/NavigateUtils";
import { SyncButton } from "../pages/chat/components/SyncButton";
import { TaskActionsMenu } from "../pages/home/components/TaskActionsMenu";
import { PulsingCircle } from "./PulsingCircle";
import styles from "./TaskItem.module.scss";
import { getRelativeTime, getStyledBranchName } from "./TaskItemUtils";
import type { ToastContent } from "./Toast";
import { Toast } from "./Toast";

type TaskItemProps = {
  projectId: string;
  task: CodingAgentTaskView;
};

export const TaskItem = ({ projectId, task }: TaskItemProps): ReactElement => {
  const [syncToast, setSyncToast] = useState<ToastContent | null>(null);
  const { navigateToChat } = useImbueNavigate();
  const { taskID } = useImbueParams();
  const [relativeTime, setRelativeTime] = useState(getRelativeTime(task.updatedAt));
  const [isHovered, setIsHovered] = useState(false);
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isArchiving, setIsArchiving] = useState(false);

  const isSelected = taskID === task.id;
  const shouldShowActions = isHovered || isSelected || isMenuOpen || task.sync.status !== LocalSyncStatus.INACTIVE;

  useEffect(() => {
    const interval = setInterval(() => {
      setRelativeTime(getRelativeTime(task.updatedAt));
    }, 30000);

    return (): void => clearInterval(interval);
  }, [task.updatedAt]);

  const handleOpenTask = useCallback((): void => {
    navigateToChat(projectId, task.id);
  }, [navigateToChat, projectId, task.id]);

  const handleKeySelect = useCallback(
    (e: React.KeyboardEvent): void => {
      if (e.key === "Enter" || e.key === " ") {
        if (e.target !== e.currentTarget) {
          return;
        }
        e.preventDefault();
        handleOpenTask();
      }
    },
    [handleOpenTask],
  );

  const actionsChild = shouldShowActions ? (
    <>
      <Flex pr="2">
        {!task.isArchived && shouldShowActions && (
          <SyncButton
            task={task}
            currentProjectID={projectId}
            toastCallback={setSyncToast}
            widgetStyle="ICON"
            buttonContext="SIDEBAR"
            disabled={isDeleting || isArchiving}
          />
        )}
        <TaskActionsMenu
          projectId={projectId}
          task={task}
          onOpenChange={setIsMenuOpen}
          isArchiving={isArchiving}
          setIsArchiving={setIsArchiving}
          isDeleting={isDeleting}
          setIsDeleting={setIsDeleting}
        />
      </Flex>
    </>
  ) : (
    <> </>
  );

  const toastChild = (
    <Toast
      open={!!syncToast}
      onOpenChange={(open) => !open && setSyncToast(null)}
      title={syncToast?.title}
      type={syncToast?.type}
    />
  );

  return RenderTaskItem({
    task: {
      id: task.id,
      title: task.title,
      initialPrompt: task.initialPrompt,
      status: task.status,
      branchName: task.branchName,
    },
    relativeTime,
    projectId,
    isSelected,
    isArchived: task.isArchived,
    isMenuOpen,
    handleOpenTask,
    handleKeySelect,
    setIsHovered,
    actions: actionsChild,
    toast: toastChild,
  });
};

type TaskItemRenderProps = {
  task: Pick<CodingAgentTaskView, "id" | "title" | "initialPrompt" | "status" | "branchName">;
  relativeTime: string;
  projectId: string;
  isSelected: boolean;
  isArchived: boolean;
  isMenuOpen: boolean;

  handleOpenTask?: () => void;
  handleKeySelect?: (e: React.KeyboardEvent) => void;
  setIsHovered?: (hovered: boolean) => void;
  actions?: ReactElement;
  toast?: ReactElement;
};

/** This method exists to isolate the rendering of the task item from the logic of pulling all the props together from
    our various hooks and data models.
 *
 */
export const RenderTaskItem = (itemRenderProps: TaskItemRenderProps): ReactElement => {
  const branchName = itemRenderProps.task.branchName ? getStyledBranchName(itemRenderProps.task) : "";

  return (
    <>
      <Flex
        direction="row"
        justify="between"
        align="center"
        className={styles.task}
        onClick={itemRenderProps.handleOpenTask}
        tabIndex={0}
        onKeyDown={itemRenderProps.handleKeySelect}
        onMouseEnter={() => itemRenderProps.setIsHovered?.(true)}
        onMouseLeave={() => itemRenderProps.setIsHovered?.(false)}
        aria-label="Open task"
        data-status={itemRenderProps.task.status}
        data-selected={itemRenderProps.isSelected}
        data-archived={itemRenderProps.isArchived}
        data-menu-open={itemRenderProps.isMenuOpen}
        width="100%"
        position="relative"
        data-testid={ElementIds.TASK_BUTTON}
      >
        <Flex direction="column" p="3" flexGrow="1" minWidth="0" width="100%">
          <Flex direction="row" gapX="3" align="center" width="100%">
            <div data-testid={ElementIds.TASK_STATUS} className={styles.statusIndicatorWrapper}>
              <StatusIndicator status={itemRenderProps.task.status} />
            </div>
            {itemRenderProps.task.title ? (
              <Text
                truncate={true}
                className={styles.primaryText}
                data-testid={ElementIds.TASK_TITLE}
                data-has-title={true}
              >
                {itemRenderProps.task.title}
              </Text>
            ) : (
              <Text
                truncate={true}
                className={styles.titleTextBuilding}
                data-testid={ElementIds.TASK_TITLE}
                data-has-title={false}
              >
                {itemRenderProps.task.initialPrompt}
              </Text>
            )}
          </Flex>
          <Flex
            data-testid={ElementIds.TASK_BRANCH}
            data-branch-name={itemRenderProps.task.branchName}
            style={{ paddingLeft: "calc(16px + 12px)" }}
          >
            {itemRenderProps.task.branchName ? (
              <Text truncate={true} className={styles.secondaryText}>
                {itemRenderProps.relativeTime} {branchName}
              </Text>
            ) : (
              <Text className={styles.secondaryText}>{itemRenderProps.relativeTime} generating branch</Text>
            )}
          </Flex>
        </Flex>
        {itemRenderProps.actions}
        {itemRenderProps.toast}
      </Flex>
    </>
  );
};

type StatusIndicatorProps = {
  status: TaskStatus;
};

export const StatusIndicator = ({ status }: StatusIndicatorProps): ReactElement => {
  switch (status) {
    case TaskStatus.BUILDING:
      return (
        <Tooltip content="Building">
          <PulsingCircle />
        </Tooltip>
      );
    case TaskStatus.RUNNING:
      return (
        <Tooltip content="Running">
          <PulsingCircle />
        </Tooltip>
      );
    case TaskStatus.READY:
      return (
        <Tooltip content="Ready">
          <div className={styles.readyStatusIcon} />
        </Tooltip>
      );
    case TaskStatus.ERROR: {
      return (
        <Tooltip content="Error">
          <div className={styles.errorStatusIcon} />
        </Tooltip>
      );
    }
    default:
      throw new Error(`Unknown task status: ${status}`);
  }
};
