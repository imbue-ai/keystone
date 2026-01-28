import { Badge, Flex, Link, Strong, Text, Tooltip } from "@radix-ui/themes";
import { ArrowUpDownIcon } from "lucide-react";
import type { ReactElement, ReactNode } from "react";
import { useState } from "react";

import { useLocalSyncState } from "~/common/state/hooks/useLocalSyncTaskStatePolling.ts";
import { useProjectPath } from "~/common/state/hooks/useProjects.ts";

import { useImbueNavigate } from "../common/NavigateUtils.ts";
import type { LocalSyncState } from "../common/state/atoms/localSyncState.ts";
import { SyncButton } from "../pages/chat/components/SyncButton.tsx";
import { getSyncingDuration } from "../pages/home/Utils.ts";
import { BlandCircle } from "./PulsingCircle.tsx";
import styles from "./SyncedTaskFooter.module.scss";
import { StatusIndicator } from "./TaskItem.tsx";
import type { ToastContent } from "./Toast.tsx";
import { Toast } from "./Toast.tsx";

type SyncedTaskFooterProps = {
  projectID: string;

  currentTaskID?: string;
};

type SyncedTaskContentProps = {
  currentTaskID?: string;
  syncedProjectPath: string | null;
  children?: ReactNode;
} & LocalSyncState;

export const SyncedTaskFooter = ({ projectID, currentTaskID }: SyncedTaskFooterProps): ReactElement | null => {
  const [toast, setToast] = useState<ToastContent | null>(null);
  const localSyncState = useLocalSyncState({ currentProjectID: projectID });
  const syncedProjectPath = useProjectPath(localSyncState?.syncedTask?.projectId ?? "") || null;

  const showToast = <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} {...(toast ?? {})} />;

  // Show the footer only if a sync session is active
  // (Stash is now shown in the header instead of footer)
  if (!localSyncState) {
    // always need to show toast in case the action sending the toast also clears all state
    return showToast;
  }

  const isSyncedToCurrentTask = localSyncState != null && localSyncState.syncedTask.id === currentTaskID;

  const content = (
    <SyncedTaskExposition {...{ currentTaskID, syncedProjectPath, ...localSyncState }}>
      {!isSyncedToCurrentTask && (
        <SyncButton
          task={localSyncState.syncedTask}
          currentProjectID={projectID}
          currentTaskID={currentTaskID}
          toastCallback={setToast}
          widgetStyle="REGULAR"
          buttonContext="OTHER_TASK"
        />
      )}
    </SyncedTaskExposition>
  );

  // TODO: push out the toasting to the top of the page, pass down as a context?
  return (
    <Flex
      className={isSyncedToCurrentTask ? styles.footerDiminished : styles.footer}
      px="5"
      py="2"
      direction="row"
      align="center"
      justify="between"
      gap="3"
    >
      {content}
      {showToast}
    </Flex>
  );
};

const SyncedTaskExposition = (props: SyncedTaskContentProps): ReactElement | null => {
  const { currentTaskID, syncedProjectPath, syncedTask, isOtherProjectSynced, children } = props;
  const navigate = useImbueNavigate();
  const isSyncedToCurrentTask = syncedTask.id === currentTaskID;
  const identifier =
    isOtherProjectSynced && syncedProjectPath
      ? `in …/${syncedProjectPath.split("/").reverse()[0]} ⟩ ${syncedTask.branchName}`
      : syncedTask.branchName;

  const agentTooltip = (
    <>
      <Strong className={styles.codeTooltip}>{syncedTask.branchName}</Strong>
      <br />
      Agent&apos;s container
    </>
  );
  const localTooltip = (
    <>
      <Strong className={styles.codeTooltip}>{syncedTask.branchName}</Strong>
      <br />
      <Text className={styles.codeTooltip}>{syncedProjectPath}</Text>
    </>
  );

  // TODO: push out the toasting to the top of the page, pass down as a context?
  return (
    <Flex direction="row" gap="2" align="center" wrap="wrap" minWidth="0">
      {isSyncedToCurrentTask ? (
        <>
          <ArrowUpDownIcon />
          <Text className={styles.primaryText}>Pairing Mode enabled for this task</Text>
        </>
      ) : (
        <>
          {syncedTask.status ? <StatusIndicator status={syncedTask.status} /> : <BlandCircle />}

          <Link
            onClick={() => navigate.navigateToChat(syncedTask.projectId, syncedTask.id)}
            className={styles.taskLink}
          >
            {syncedTask.titleOrSomethingLikeIt}
          </Link>
          <Badge>{identifier}</Badge>
        </>
      )}

      <Text className={styles.secondaryText}>
        Changes are being automatically synced between the{" "}
        <Tooltip content={agentTooltip} delayDuration={0}>
          <Text className={styles.tooltipEmphasis}>Agent</Text>
        </Tooltip>{" "}
        and your{isOtherProjectSynced ? " other " : " "}
        <Tooltip content={localTooltip} delayDuration={0}>
          <Text className={styles.tooltipEmphasis}>Local</Text>
        </Tooltip>{" "}
        directory
      </Text>
      <Flex align="center" gap="3" flexShrink="0" ml="auto">
        <Text className={styles.secondaryText}>{getSyncingDuration(syncedTask.syncStartedAt)}</Text>
        {children}
      </Flex>
    </Flex>
  );
};
