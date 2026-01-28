import {
  AlertDialog,
  Box,
  Button,
  Flex,
  Popover,
  ScrollArea,
  Separator,
  Spinner,
  Strong,
  Text,
  Tooltip,
} from "@radix-ui/themes";
import { useAtomValue } from "jotai";
import {
  ArchiveIcon,
  ArchiveRestoreIcon,
  CheckIcon,
  ChevronDown,
  ChevronRight,
  InfoIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { type ReactElement, useMemo, useState } from "react";

import type { LocalSyncStatus } from "~/api";
import { deleteSyncStash, restoreSyncStash } from "~/api/sdk.gen.ts";
import { HTTPException } from "~/common/Errors.ts";
import { sculptorStashSingletonStateAtom } from "~/common/state/atoms/sculptorStashSingleton.ts";
import { useLocalRepoInfo } from "~/common/state/hooks/useLocalRepoInfo.ts";
import { useProjectPath } from "~/common/state/hooks/useProjects.ts";
import type { ToastContent } from "~/components/Toast.tsx";
import { ToastType } from "~/components/Toast.tsx";
import { getHumanDuration } from "~/pages/home/Utils.ts";

import styles from "./StashPanel.module.scss";

type StashPanelProps = {
  currentProjectID: string;
  setToast: (toast: ToastContent) => void;
  syncStatus: LocalSyncStatus | null;
  isPanelOpen: boolean;
  setIsPanelOpen: (open: boolean) => void;
};

type NoticeType = "success" | "warning" | "error" | "info" | "loading";

type Notice = {
  message: string;
  type: NoticeType;
  details?: string;
  final?: true;
};

/**
 * Button with popover panel that shows when a stash exists for the current or another project.
 * The button is always visible when a stash exists. The panel allows restoration (when allowed)
 * and deletion (always allowed, even during sync).
 */
export const StashPanel = ({
  currentProjectID,
  syncStatus,
  setToast,
  isPanelOpen,
  setIsPanelOpen,
}: StashPanelProps): ReactElement | null => {
  const { stashSingleton } = useAtomValue(sculptorStashSingletonStateAtom) || {};
  const { status: repoStatus } = useLocalRepoInfo(stashSingleton?.owningProjectId || currentProjectID) || {};

  const stashProjectPath = useProjectPath(stashSingleton?.owningProjectId || currentProjectID);
  const isSyncEnabled = syncStatus !== null && syncStatus !== undefined;
  const isSyncPaused = syncStatus === "PAUSED";

  // const isOtherTaskSynced = syncState && syncState.syncedTask.id !== currentProjectID;
  const buttonIcon = isSyncEnabled ? <ArchiveIcon size={16} /> : <ArchiveRestoreIcon size={16} />;

  const stateCssClass = isSyncPaused || !isSyncEnabled ? styles.warning : isSyncEnabled ? styles.active : "";
  const stashButtonClass = `${styles.stashButton} ${stateCssClass}`.trim();

  const [isRestoring, setIsRestoring] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
  const [noticesState, setNoticesState] = useState<Array<Notice>>([]);

  // Don't show if there's no stash
  if (!stashSingleton) {
    return null;
  }

  const {
    stash: { enabledTransition, sourceBranch, absoluteStashRef },
  } = stashSingleton;
  const commitHash = enabledTransition.fromPosition.commitHash.substring(0, 8);

  let derivedBlocker: Notice | null = null;
  if (noticesState.filter((n) => n.type === "success").length === 0) {
    if (isSyncEnabled) {
      const message = `Cannot restore while Pairing Mode is active${isSyncPaused ? " or paused" : ""}.`;
      derivedBlocker = { type: "info", message };
    } else if (repoStatus?.isInIntermediateState) {
      const acting = repoStatus.isMerging
        ? "merging"
        : repoStatus.isRebasing
          ? "rebasing"
          : repoStatus.isCherryPicking
            ? "cherry-picking"
            : "in intermediate state";
      const message = `Cannot restore while ${acting}.`;
      derivedBlocker = { type: "warning", message };
    } else if (repoStatus && !repoStatus.files.areCleanIncludingUntracked) {
      const message = `Cannot restore with uncommitted or unstaged changes: ${repoStatus.files.description}`;
      derivedBlocker = { type: "warning", message };
    }
  }
  const notices = derivedBlocker ? [derivedBlocker, ...noticesState] : noticesState;
  const canRestore = !isSyncEnabled && derivedBlocker == null;

  // Format the time ago
  let durAgo = getHumanDuration(enabledTransition.fromPosition.createdAt || null);
  if (durAgo) {
    durAgo = ` ${durAgo} ago`;
  }

  const attemptRestore = async (): Promise<void> => {
    if (!canRestore) {
      return;
    }

    setIsRestoring(true);
    setNoticesState([{ type: "loading", message: "Restoring stash..." }]);

    try {
      const { data } = await restoreSyncStash({
        path: { project_id: stashSingleton.owningProjectId },
        body: { absoluteStashRef },
      });
      const title = data
        ? `Stash restored into ${data.currentBranch}. Restored file state: ${data.status.files.description}.`
        : "Stash restored.";
      setToast({ title, type: ToastType.SUCCESS });
      setNoticesState([{ type: "success", message: "Stash restored successfully", final: true }]);
      setIsPanelOpen(false);
    } catch (error) {
      const errorMessage = error instanceof HTTPException ? error.detail : String(error);
      const title = `Failed to restore stash: ${errorMessage}`;
      setToast({ title, type: ToastType.ERROR });
      setNoticesState([
        {
          type: "error",
          message: "Failed to restore stash",
          details: errorMessage,
          final: true,
        },
      ]);
    } finally {
      setIsRestoring(false);
    }
  };

  const confirmDelete = async (): Promise<void> => {
    setIsDeleteDialogOpen(false);
    setIsDeleting(true);
    setNoticesState([{ type: "loading", message: "Deleting stash..." }]);

    try {
      await deleteSyncStash({
        path: { project_id: stashSingleton.owningProjectId },
        body: { absoluteStashRef },
      });
      setToast({ title: "Stash deleted successfully", type: ToastType.SUCCESS });
      setNoticesState([{ type: "success", message: "Stash deleted successfully", final: true }]);
      setIsPanelOpen(false);
    } catch (error) {
      const errorMessage = error instanceof HTTPException ? error.detail : String(error);
      const title = `Failed to delete stash: ${errorMessage}`;
      setToast({ title, type: ToastType.ERROR });
      setNoticesState([
        {
          type: "error",
          message: "Failed to delete stash",
          details: errorMessage,
          final: true,
        },
      ]);
    } finally {
      setIsDeleting(false);
    }
  };

  const isOperationInProgress = isRestoring || isDeleting;

  const deleteTheStashSpan = (
    <Text
      as="span"
      className={styles.deleteLink}
      onClick={isOperationInProgress ? undefined : (): void => setIsDeleteDialogOpen(true)}
      style={{ cursor: isOperationInProgress ? "not-allowed" : "pointer" }}
    >
      delete the stash
    </Text>
  );

  const restoreTheStashButton = (
    <Button
      onClick={attemptRestore}
      disabled={isOperationInProgress || !canRestore}
      size="3"
      mt="4"
      style={{ width: "100%" }}
    >
      {isRestoring ? (
        <>
          <Spinner size="2" />
          <Text>Restoring...</Text>
        </>
      ) : (
        <>
          <ArchiveRestoreIcon size={16} />
          <Text>Restore Stash</Text>
        </>
      )}
    </Button>
  );

  const isPanelDisplayedAsOpen = isPanelOpen || isDeleteDialogOpen || isOperationInProgress;
  // TODO: Add stash step to starting/stopping flows
  return (
    <>
      <AlertDialog.Root open={isDeleteDialogOpen} onOpenChange={setIsDeleteDialogOpen}>
        <AlertDialog.Content style={{ maxWidth: 450 }}>
          <AlertDialog.Title>Are you sure you want to delete the stash?</AlertDialog.Title>
          <AlertDialog.Description size="2">
            This action cannot be undone. The stash will be permanently deleted.
          </AlertDialog.Description>

          <Flex gap="3" mt="4" justify="end">
            <AlertDialog.Cancel>
              <Button variant="soft" color="gray">
                Cancel
              </Button>
            </AlertDialog.Cancel>
            <AlertDialog.Action>
              <Button variant="solid" color="red" onClick={confirmDelete}>
                Delete Stash
              </Button>
            </AlertDialog.Action>
          </Flex>
        </AlertDialog.Content>
      </AlertDialog.Root>

      <Popover.Root open={isPanelDisplayedAsOpen} onOpenChange={setIsPanelOpen}>
        {/* disableHoverableContent doesn't seem to do anything... */}
        <Tooltip content="View Stash Info" open={isPanelDisplayedAsOpen ? false : undefined}>
          <Popover.Trigger>
            <Button variant="solid" size="1" className={stashButtonClass}>
              {isSyncEnabled ? (
                <Text className={`${styles.buttonText} ${styles.textEmpty}`} />
              ) : (
                <Text className={styles.buttonText}>Stash</Text>
              )}
              {buttonIcon}
            </Button>
          </Popover.Trigger>
        </Tooltip>
        <Popover.Content className={styles.panel}>
          <Box p="4">
            <Text size="3" weight="medium" className={styles.panelTitle}>
              Stash Information
            </Text>

            <Flex direction="column" gap="3" mt="3">
              <Flex direction="row" gap="2" align="start">
                <Text size="2" className={styles.panelLabel} style={{ minWidth: "70px" }}>
                  <Strong>Source</Strong>
                </Text>
                <Text size="2" className={styles.panelText}>
                  <span className={styles.codeText}>
                    {sourceBranch}@{commitHash}
                  </span>
                </Text>
              </Flex>

              <Flex direction="row" gap="2" align="start">
                <Text size="2" className={styles.panelLabel} style={{ minWidth: "70px" }}>
                  <Strong>Project</Strong>
                </Text>
                <Text size="2" className={styles.panelText} style={{ wordBreak: "break-all" }}>
                  <span className={styles.codeText}>{stashProjectPath}</span>
                </Text>
              </Flex>

              <Flex direction="row" gap="2" align="start">
                <Text size="2" className={styles.panelLabel} style={{ minWidth: "70px" }}>
                  <Strong>Created</Strong>
                </Text>
                <Text size="2" className={styles.panelText}>
                  {durAgo || "recently"}
                </Text>
              </Flex>
            </Flex>

            {isSyncEnabled ? (
              <>
                {!isSyncPaused ? (
                  <Text size="2" className={styles.helpText}>
                    Managed by Pairing Mode and will auto-restore at the end of the session.
                  </Text>
                ) : (
                  <Text size="2" className={styles.helpText}>
                    Will be left for triage if Pairing Mode is stopped from the current <b>paused</b> state.
                  </Text>
                )}
                <Text size="2" className={styles.helpText}>
                  Alternatively, you can {deleteTheStashSpan}
                </Text>
              </>
            ) : !canRestore ? (
              <>
                <Text size="2" className={styles.helpText}>
                  Resolve the blocker below, then click <Strong>Restore stash</Strong> to restore the backup that
                  Pairing Mode created of your local changes.
                </Text>
                <Text size="2" className={styles.helpText}>
                  Alternatively, you can {deleteTheStashSpan}
                </Text>
              </>
            ) : (
              <Text size="2" className={styles.helpText}>
                Click <Strong>Restore stash</Strong> to restore the backup that Pairing Mode created of your local
                changes, or {deleteTheStashSpan}
              </Text>
            )}

            {canRestore ? (
              restoreTheStashButton
            ) : (
              <Tooltip content={canRestore ? "" : "Cannot restore due to issues listed below"}>
                {restoreTheStashButton}
              </Tooltip>
            )}
          </Box>

          {notices && notices.length > 0 && (
            <>
              <Separator size="4" className={styles.separator} />
              <NoticesList notices={notices} />
            </>
          )}
        </Popover.Content>
      </Popover.Root>
    </>
  );
};

// Supporting components for notices

type ExpandableNoticeProps = {
  notice: Notice;
};

const ExpandableNotice = ({ notice }: ExpandableNoticeProps): ReactElement => {
  const [isExpanded, setIsExpanded] = useState(false);

  const color: "green" | "red" | undefined = useMemo((): "green" | "red" | undefined => {
    if (!notice.final) {
      return undefined;
    }

    if (notice.type === "success") {
      return "green";
    }

    if (notice.type === "error") {
      return "red";
    }
  }, [notice.type, notice.final]);

  const icon = useMemo((): ReactElement => {
    switch (notice.type) {
      case "loading":
        return <Spinner />;
      case "error":
      case "warning":
        return <TriangleAlertIcon color={color} />;
      case "success":
        return <CheckIcon color={color} />;
      case "info":
        return <InfoIcon />;
    }
  }, [notice.type, color]);

  return (
    <Flex gapX="3" align="start">
      <Flex align="start" pt="3px">
        {icon}
      </Flex>
      <Flex direction="row" align="start" style={{ flex: 1 }}>
        <Text size="2" role="listitem" className={styles.notice} data-notice-type={notice.type}>
          <Text color={color}>{notice.message}</Text>
          {notice.details && (
            <Popover.Root open={isExpanded} onOpenChange={setIsExpanded}>
              <Popover.Trigger>
                <Button variant="ghost" size="1" mt="2px" style={{ padding: "4px", marginLeft: "2px" }}>
                  {isExpanded && <ChevronDown />}
                  {!isExpanded && <ChevronRight />}
                </Button>
              </Popover.Trigger>
              <Popover.Content style={{ padding: 0 }}>
                <PreformattedTextView content={notice.details} />
              </Popover.Content>
            </Popover.Root>
          )}
        </Text>
      </Flex>
    </Flex>
  );
};

type NoticesListProps = {
  notices: Array<Notice>;
};

const NoticesList = ({ notices }: NoticesListProps): ReactElement => {
  return (
    <Flex className={styles.noticeSection} p="5" gapY="4" direction="column" role="list">
      {notices.map((notice, i) => (
        <ExpandableNotice key={i} notice={notice} />
      ))}
    </Flex>
  );
};

type PreformattedTextViewProps = {
  content: string;
};

const PreformattedTextView = ({ content }: PreformattedTextViewProps): ReactElement => {
  const handleCopyText = (): void => {
    navigator.clipboard.writeText(content);
  };

  return (
    <Flex className={styles.textViewContainer}>
      <Button variant="ghost" onClick={handleCopyText}>
        Copy
      </Button>
      <ScrollArea>
        <pre>{content}</pre>
      </ScrollArea>
    </Flex>
  );
};
