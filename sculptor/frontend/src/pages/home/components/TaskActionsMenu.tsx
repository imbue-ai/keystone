import { AlertDialog, Button, DropdownMenu, Flex, Spinner } from "@radix-ui/themes";
import { Archive, Copy, MoreVertical, RotateCcw, Trash2 } from "lucide-react";
import type { ReactElement } from "react";
import { useState } from "react";

import type { CodingAgentTaskView } from "../../../api";
import { archiveTask, deleteTask, ElementIds, restoreTask, TaskStatus } from "../../../api";
import { useImbueLocation, useImbueNavigate, useImbueParams } from "../../../common/NavigateUtils.ts";
import { Toast, type ToastContent, ToastType } from "../../../components/Toast.tsx";
import { TooltipIconButton } from "../../../components/TooltipIconButton.tsx";
import { getBranchName } from "../Utils.ts";
import styles from "./TaskActionsMenu.module.scss";

type TaskActionsMenuProps = {
  projectId: string;
  task: CodingAgentTaskView;
  isDeleting: boolean;
  setIsDeleting: (isDeleting: boolean) => void;
  isArchiving: boolean;
  setIsArchiving: (isArchiving: boolean) => void;
  onOpenChange?: (open: boolean) => void;
};

export const TaskActionsMenu = ({
  projectId,
  task,
  isDeleting,
  setIsDeleting,
  isArchiving,
  setIsArchiving,
  onOpenChange,
}: TaskActionsMenuProps): ReactElement => {
  const { isChatRoute } = useImbueLocation();
  const { navigateToHome } = useImbueNavigate();
  const { taskID } = useImbueParams();
  const [isRestoring, setIsRestoring] = useState(false);
  const [shouldShowCopyToast, setShouldShowCopyToast] = useState(false);
  const [shouldShowDeleteDialog, setShouldShowDeleteDialog] = useState(false);
  const [actionToast, setActionToast] = useState<ToastContent | null>(null);

  const handleDelete = async (): Promise<void> => {
    setIsDeleting(true);

    try {
      await deleteTask({
        path: { project_id: projectId, task_id: task.id },
      });
      setActionToast({ title: "Task deleted successfully", type: ToastType.SUCCESS });
      setShouldShowDeleteDialog(false);
      if (isChatRoute) {
        if (taskID === task.id) {
          navigateToHome(projectId);
        }
      }
    } catch (error) {
      console.error("Failed to delete task:", error);
      setActionToast({ title: "Failed to delete task", type: ToastType.ERROR });
    }

    setIsDeleting(false);
  };

  const handleArchive = async (): Promise<void> => {
    setIsArchiving(true);
    const isArchivingTask = !task.isArchived;

    try {
      await archiveTask({
        path: { project_id: projectId, task_id: task.id },
        body: { isArchived: isArchivingTask },
      });
      setActionToast({
        title: `Task ${isArchivingTask ? "archived" : "unarchived"} successfully`,
        type: ToastType.SUCCESS,
      });
    } catch (error) {
      console.error("Failed to archive/unarchive task:", error);
      setActionToast({ title: `Failed to ${isArchivingTask ? "archive" : "unarchive"} task`, type: ToastType.ERROR });
    }

    setIsArchiving(false);
  };

  const handleCopyBranchName = (): void => {
    const branchName = getBranchName(task);
    if (!branchName) {
      return;
    }
    navigator.clipboard.writeText(branchName);
    setShouldShowCopyToast(true);
  };

  const handleRestore = async (): Promise<void> => {
    setIsRestoring(true);

    try {
      await restoreTask({
        path: { project_id: projectId, task_id: task.id },
      });
      setActionToast({ title: "Task restored successfully", type: ToastType.SUCCESS });
    } catch (error) {
      console.error("Failed to restore task:", error);
      setActionToast({ title: "Failed to restore task", type: ToastType.ERROR });
    }

    setIsRestoring(false);
  };

  return (
    <DropdownMenu.Root open={isArchiving ? true : undefined} onOpenChange={onOpenChange}>
      <DropdownMenu.Trigger>
        <div className={styles.menuTriggerWrapper} onClick={(e) => e.stopPropagation()}>
          <TooltipIconButton
            variant="ghost"
            size="1"
            className={styles.dropdownMenu}
            tooltipText="Task actions"
            aria-label="Task actions"
            data-testid={ElementIds.TASK_ACTIONS_MENU_BUTTON}
          >
            <MoreVertical width={14} height={14} />
          </TooltipIconButton>
        </div>
      </DropdownMenu.Trigger>
      <DropdownMenu.Content size="1">
        <DropdownMenu.Item
          onClick={(e) => {
            handleCopyBranchName();
            e.stopPropagation();
          }}
          disabled={isArchiving}
        >
          <Copy width={14} height={14} />
          Copy Branch Name
        </DropdownMenu.Item>
        {task.status === TaskStatus.ERROR && (
          <>
            <DropdownMenu.Separator />
            <DropdownMenu.Item
              onClick={(e) => {
                e.stopPropagation();
                handleRestore();
              }}
              disabled={isRestoring}
              data-testid={ElementIds.RESTORE_MENU_ITEM}
            >
              <RotateCcw width={14} height={14} />
              Restore
            </DropdownMenu.Item>
          </>
        )}
        <DropdownMenu.Separator />
        <DropdownMenu.Item
          onClick={(e) => {
            e.stopPropagation();
            handleArchive();
          }}
          disabled={isArchiving || isDeleting}
          data-testid={ElementIds.ARCHIVE_MENU_ITEM}
        >
          {isArchiving ? <Spinner size="1" /> : <Archive width={14} height={14} />}
          {isArchiving
            ? task.isArchived
              ? "Unarchiving..."
              : "Archiving..."
            : task.isArchived
              ? "Unarchive"
              : "Archive"}
        </DropdownMenu.Item>
        <DropdownMenu.Item
          color="red"
          onClick={(e) => {
            e.stopPropagation();
            setShouldShowDeleteDialog(true);
          }}
          disabled={isDeleting || isArchiving}
          data-testid={ElementIds.DELETE_MENU_ITEM}
        >
          <Trash2 width={14} height={14} />
          Delete
        </DropdownMenu.Item>
      </DropdownMenu.Content>
      <Toast open={shouldShowCopyToast} onOpenChange={setShouldShowCopyToast} title="Branch name copied to clipboard" />
      <Toast
        open={!!actionToast}
        onOpenChange={(open) => !open && setActionToast(null)}
        title={actionToast?.title}
        type={actionToast?.type}
      />

      <AlertDialog.Root open={shouldShowDeleteDialog} onOpenChange={setShouldShowDeleteDialog}>
        <AlertDialog.Content size="2">
          <AlertDialog.Title>Delete Task</AlertDialog.Title>
          <AlertDialog.Description>
            Are you sure you want to delete task &ldquo;{task.title || task.initialPrompt}&rdquo;? This action cannot be
            undone.
          </AlertDialog.Description>

          <Flex gap="3" mt="4" justify="end">
            <AlertDialog.Cancel>
              <Button variant="soft" color="gray" onClick={(e) => e.stopPropagation()}>
                Cancel
              </Button>
            </AlertDialog.Cancel>
            <AlertDialog.Action>
              <Button
                variant="solid"
                color="red"
                onClick={(e) => {
                  e.stopPropagation();
                  handleDelete();
                }}
                disabled={isDeleting}
                data-testid={ElementIds.CONFIRM_DELETE_BUTTON}
              >
                Delete
              </Button>
            </AlertDialog.Action>
          </Flex>
        </AlertDialog.Content>
      </AlertDialog.Root>
    </DropdownMenu.Root>
  );
};
