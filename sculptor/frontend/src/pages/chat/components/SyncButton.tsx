import { Cross1Icon } from "@radix-ui/react-icons";
import type { TooltipProps } from "@radix-ui/themes";
import { Box, Button, Flex, Spinner, Text, Tooltip } from "@radix-ui/themes";
import { useAtomValue, useSetAtom } from "jotai";
import {
  ArrowUpDownIcon,
  CheckIcon,
  CircleOffIcon,
  CirclePauseIcon,
  RefreshCwIcon,
  RefreshCwOffIcon,
} from "lucide-react";
import type { ReactElement } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";

import type { LocalSyncState, SyncedTaskView } from "~/api";
import {
  disableTaskSync,
  ElementIds,
  enableTaskSync,
  LocalSyncDisabledActionTaken,
  LocalSyncSetupStep,
  LocalSyncStatus,
  LocalSyncTeardownStep,
  TaskStatus,
} from "~/api";
import { HTTPException } from "~/common/Errors.ts";
import { updateLocalRepoInfoAtom } from "~/common/state/atoms/localRepoInfo";
import { localSyncStateAtom } from "~/common/state/atoms/localSyncState.ts";
import { sculptorStashSingletonStateAtom } from "~/common/state/atoms/sculptorStashSingleton.ts";
import { isPairingModeStashingBetaFeatureOnAtom } from "~/common/state/atoms/userConfig.ts";
import { useLocalRepoInfo } from "~/common/state/hooks/useLocalRepoInfo.ts";
import { useLocalSyncState } from "~/common/state/hooks/useLocalSyncTaskStatePolling";
import { useProjectPath } from "~/common/state/hooks/useProjects.ts";
import { useTask } from "~/common/state/hooks/useTaskHelpers.ts";
import { PopoverTooltip } from "~/components/PopoverTooltip.tsx";
import { StashPanel } from "~/components/StashPanel.tsx";
import { type ToastContent, ToastType } from "~/components/Toast.tsx";
import { PairingModePopoverTooltip } from "~/pages/chat/components/PairingModePopoverTooltip.tsx";
import { StashWarningModalProvider } from "~/pages/chat/components/StashWarningModalProvider";

import styles from "./SyncButton.module.scss";

export type SyncButtonState =
  | "INACTIVE"
  | "STARTING"
  | "ACTIVE_SYNCING"
  | "ACTIVE"
  | "PAUSED"
  | "STOPPING"
  | "STOP_SYNCING"
  | "ERROR";

export type SyncButtonStyle = "ICON" | "REGULAR";

/**
 * Identifies the context in which this button is being used:
 * - SIDEBAR: Small icon button in task list sidebar
 * - PRIMARY: Main pairing mode button in header for currently selected task
 * - OTHER_TASK: Button in footer for a different task that has pairing enabled
 */
export type SyncButtonContext = "SIDEBAR" | "PRIMARY" | "OTHER_TASK";

const ORDERED_SETUP_STEPS: Array<LocalSyncSetupStep> = [
  LocalSyncSetupStep.VALIDATE_GIT_STATE_SAFETY,
  LocalSyncSetupStep.MIRROR_AGENT_INTO_LOCAL_REPO,
  LocalSyncSetupStep.BEGIN_TWO_WAY_CONTROLLED_SYNC,
] as const;

const ORDERED_TEARDOWN_STEPS: Array<LocalSyncTeardownStep> = [
  LocalSyncTeardownStep.STOP_FILE_SYNC,
  LocalSyncTeardownStep.RESTORE_LOCAL_FILES,
  LocalSyncTeardownStep.RESTORE_ORIGINAL_BRANCH,
] as const;

function isPrimaryPairingButton(props: SyncButtonProps): boolean {
  return props.buttonContext === "PRIMARY";
}

function isOtherTaskPairingButton(props: SyncButtonProps): boolean {
  return props.buttonContext === "OTHER_TASK";
}

type SyncButtonProps = {
  task: SyncedTaskView;
  currentProjectID: string;

  widgetStyle: SyncButtonStyle;

  buttonContext: SyncButtonContext;

  currentTaskID?: string;

  loading?: boolean;
  disabled?: boolean;

  toastCallback: (toast: ToastContent) => void;

  showStopWithoutHover?: boolean;

  forceState?: LocalSyncStatus;
};

const wait30sForSlowServerAction = {
  wsTimeout: 30000,
  timeout: 30000,
};

export const SyncButton = (props: SyncButtonProps): ReactElement => {
  const [isMainButtonHovered, setIsMainButtonHovered] = useState<boolean>(false);
  const [didLastOperationFail, setDidLastOperationFail] = useState<boolean>(false);
  const [isStashPanelOpenUnlessOverridden, setIsStashPanelOpenUnlessOverridden] = useState<boolean>(false);

  // Track if we had a startup failure (went from STARTING → STOPPING without reaching ACTIVE)
  const wasStarting = useRef<boolean>(false);
  const everReachedActive = useRef<boolean>(false);

  const { status: repoStatus, currentBranch } = useLocalRepoInfo(props.currentProjectID) || {};
  const isPairingModeStashingEnabled = useAtomValue(isPairingModeStashingBetaFeatureOnAtom);
  const setSculptorStashSingletonState = useSetAtom(sculptorStashSingletonStateAtom);

  // Use new global sync polling endpoint instead of task-specific sync
  const localSyncState = useLocalSyncState({ currentProjectID: props.currentProjectID });
  const setLocalSyncState = useSetAtom(localSyncStateAtom);

  const { syncedTask, isOtherProjectSynced } = localSyncState ?? {};
  const isOtherTaskSynced = syncedTask && syncedTask.id !== props.task.id;
  const syncedProjectPath = useProjectPath(syncedTask?.projectId ?? "") || null;

  const stashSingletonState = useAtomValue(sculptorStashSingletonStateAtom);
  const isStashBlockingNewSync = stashSingletonState != null && syncedTask == null;
  const stashSourceBranch = stashSingletonState?.stashSingleton.stash.sourceBranch || null;
  const updateLocalRepoInfo = useSetAtom(updateLocalRepoInfoAtom);

  // Debounce the local sync state to avoid flickering between ACTIVE_SYNCING and ACTIVE
  const debouncedButtonState = useDebouncedSyncState(
    props.task.sync,
    200,
    (prev, current) => prev?.status === LocalSyncStatus.ACTIVE_SYNCING && current?.status === LocalSyncStatus.ACTIVE,
  );

  const syncState: SyncButtonState = useMemo((): SyncButtonState => {
    if (props.forceState) {
      return props.forceState;
    }

    return debouncedButtonState?.status ?? "INACTIVE";
  }, [debouncedButtonState, props.forceState]);

  // we don't want to thrash around the stash button during a transition
  // TODO: https://linear.app/imbue/issue/PROD-3296/localsyncservice-should-send-a-switching-message-for-ui-smoothing
  const lastStashRelevantSyncStatus = useRef<LocalSyncStatus | null>(null);
  useEffect(() => {
    const transientStatuses: Array<LocalSyncStatus | null | undefined> = [
      LocalSyncStatus.STARTING,
      LocalSyncStatus.STOPPING,
    ];
    if (transientStatuses.includes(syncedTask?.sync.status)) {
      return;
    }
    lastStashRelevantSyncStatus.current = syncedTask?.sync.status ?? null;
  }, [syncedTask?.sync.status]);

  // Track state transitions to detect startup failures
  useEffect(() => {
    const currentStatus = props.task.sync?.status;
    if (currentStatus === LocalSyncStatus.STARTING) {
      wasStarting.current = true;
      everReachedActive.current = false;
    } else if (currentStatus === LocalSyncStatus.ACTIVE || currentStatus === LocalSyncStatus.ACTIVE_SYNCING) {
      everReachedActive.current = true;
    } else if (currentStatus === LocalSyncStatus.STOPPING && wasStarting.current && !everReachedActive.current) {
      // We went STARTING → STOPPING without reaching ACTIVE = startup failure!
      flushSync(() => {
        setDidLastOperationFail(true);
      });
    }
  }, [props.task.sync?.status]);

  const reasonsToDisable = useMemo((): Array<string> => {
    if (syncState !== "INACTIVE") {
      // keep the button enabled if the sync is active or paused on this task
      return [];
    }

    if (props.task.status === TaskStatus.BUILDING) {
      return ["Sandbox is not ready yet, wait for the building to finish"];
    }

    if (props.task.status === TaskStatus.ERROR) {
      return ["Cannot enable Pairing Mode while task sandbox is in an error state"];
    }

    if (isOtherTaskSynced && isPrimaryPairingButton(props)) {
      if (syncedTask.sync.status === LocalSyncStatus.STARTING) {
        return ["Another agent is starting Pairing Mode. Wait for it to finish before pairing with this agent."];
      }

      if (syncedTask.sync.status === LocalSyncStatus.STOPPING) {
        return ["Another agent is stopping Pairing Mode. Wait for it to finish before pairing with this agent."];
      }
    }

    if (isOtherTaskSynced && syncedTask.sync.status === "PAUSED") {
      const projectPathInfo = isOtherProjectSynced ? ` at ${syncedProjectPath}` : "";
      return [
        `Another task's Pairing Mode session is currently in paused${projectPathInfo}. Address its issues or stop it before Pairing with this one.`,
      ];
    }

    const mustRestoreMessage = isPrimaryPairingButton(props)
      ? ["Pairing Mode disabled due to existing stash, click to resolve."]
      : ["Restore the stash to starting Pairing Mode."];

    // NOTE: We are allowing Pairing Mode to start if local repository information is not available.
    //       this is to protect the feature and fall back to backend checking if our git state
    //       polling fails.
    if (!repoStatus || repoStatus.isCleanAndSafeToOperateOn) {
      // As long as we're doing sync->sync transitions we don't have to worry about this.
      //
      // Technically, we could allow starting a sync from a clean state and just leave the stash around,
      // but then we'd have to handle the status state race in the backend more carefully.
      // So for now we're keeping it simple.
      if (isStashBlockingNewSync) {
        return mustRestoreMessage;
      }
      return [];
    }

    // Check if there's another task in non-paused state.
    //
    // We do not care about local repo's files state if it's under the management of Sync already,
    // but we do take extra care if there's an ongoing merge, cherry-picking, or rebase.
    // The latter set of options is redundant with Sync switching to paused state itself but better safe than sorry!
    const isAbleToSwitchWithinProject =
      !isOtherProjectSynced && syncedTask?.sync.status === "ACTIVE" && !repoStatus.isInIntermediateState;
    if (isAbleToSwitchWithinProject) {
      return [];
    }

    const lines: Array<string> = [];

    if (repoStatus.isInIntermediateState) {
      lines.push("the ongoing merge or other operation needs to be resolved");
    }
    const areFilesDirty = !repoStatus.files.areCleanIncludingUntracked;
    const isStashable = !isStashBlockingNewSync && isPairingModeStashingEnabled;
    if (areFilesDirty && !isStashable) {
      // lines.push("local changes need to be committed unless auto-stashing is enabled");
      lines.push("local changes need to be committed");
      lines.push("");
      lines.push(...repoStatus.files.description.split("\n"));
    } else if (areFilesDirty && isStashBlockingNewSync) {
      lines.push("First, resolve local changes:");
      lines.push("");
      lines.push(...repoStatus.files.description.split("\n"));
    }

    if (isStashBlockingNewSync) {
      if (lines.length == 0) {
        return mustRestoreMessage;
      }
      lines.push("");
      lines.push("Then, triage the existing stash from");
      lines.push(`${stashSourceBranch}.`);
    }

    if (lines.length === 0) {
      return [];
    }

    return [
      "To start Pairing Mode, please address the following issues",
      isOtherProjectSynced ? `in this task's repo at ${syncedProjectPath}:` : "in your local repository:",
      ...lines,
      // JSON.stringify(status);
    ];
  }, [
    props,
    repoStatus,
    syncState,
    syncedTask,
    isOtherTaskSynced,
    isOtherProjectSynced,
    syncedProjectPath,
    isPairingModeStashingEnabled,
    isStashBlockingNewSync,
    stashSourceBranch,
  ]);

  const clickActionIfEnabled = ((): "ON" | "OFF" => {
    if (syncState === "INACTIVE") {
      return "ON";
    }
    return "OFF";
  })();

  const isTransitioning = syncState === LocalSyncStatus.STARTING || syncState === LocalSyncStatus.STOPPING;
  const isDisabled = props.disabled || reasonsToDisable.length > 0 || isTransitioning;
  const isLoading = props.loading || !props.task.sync;

  const currentlySelectedTask = useTask(props.currentTaskID ?? "");

  // Check if there's another task that's starting (to deprioritize showing stopping progress)
  // For OTHER_TASK button: check if the current/primary task is STARTING
  // For PRIMARY button: check if syncedTask (from global atom) is STARTING
  const isAnotherTaskStarting = isOtherTaskPairingButton(props)
    ? currentlySelectedTask?.sync?.status === LocalSyncStatus.STARTING
    : syncedTask && syncedTask.id !== props.task.id && syncedTask.sync.status === LocalSyncStatus.STARTING;

  // Delay the sync state change from STARTING/STOPPING to completion
  const debouncedTransitionState = useDebouncedSyncState(
    props.task.sync,
    1500,
    // Debounce when we transition from (STARTING/STOPPING) to anything else, e.g. (ACTIVE/INACTIVE)
    (prev, current) =>
      (prev?.status === LocalSyncStatus.STARTING || prev?.status === LocalSyncStatus.STOPPING) &&
      current?.status !== LocalSyncStatus.STARTING &&
      current?.status !== LocalSyncStatus.STOPPING,
  );

  // Clear didLastOperationFail and reset refs when debounced state reaches INACTIVE
  useEffect(() => {
    if (
      props.task.sync?.status === LocalSyncStatus.INACTIVE &&
      debouncedTransitionState?.status === LocalSyncStatus.INACTIVE
    ) {
      setDidLastOperationFail(false);
      wasStarting.current = false;
      everReachedActive.current = false;
    }
  }, [props.task.sync?.status, debouncedTransitionState?.status]);

  const isStartupFailure =
    debouncedTransitionState?.status === LocalSyncStatus.STOPPING && wasStarting.current && !everReachedActive.current;

  const isThisTaskStarting = debouncedTransitionState?.status === LocalSyncStatus.STARTING || isStartupFailure;
  const isThisTaskStopping = debouncedTransitionState?.status === LocalSyncStatus.STOPPING && !isStartupFailure;

  const shouldShowProgressTooltip =
    (isPrimaryPairingButton(props) || isOtherTaskPairingButton(props)) &&
    (isThisTaskStarting || (isThisTaskStopping && !isAnotherTaskStarting));

  const shouldShowInstructionalPopover =
    !isStashPanelOpenUnlessOverridden &&
    !shouldShowProgressTooltip &&
    isPrimaryPairingButton(props) &&
    syncState === "INACTIVE" &&
    !isDisabled;

  const canShowTooltip = !shouldShowInstructionalPopover && !shouldShowProgressTooltip;

  // Force the tooltip to stay open for pairing mode progress
  const isSyncPopoverOpen = shouldShowProgressTooltip
    ? true
    : isStashPanelOpenUnlessOverridden
      ? false
      : canShowTooltip
        ? false
        : undefined;

  const startLocalSync = async (): Promise<ToastContent> => {
    setDidLastOperationFail(false);
    wasStarting.current = true;
    everReachedActive.current = false;
    try {
      const {
        data: { newlyCreatedStash },
      } = await enableTaskSync({
        path: { project_id: props.currentProjectID, task_id: props.task.id },
        body: { isStashingOk: isPairingModeStashingEnabled },
        meta: wait30sForSlowServerAction,
      });
      if (newlyCreatedStash) {
        setSculptorStashSingletonState({
          stashSingleton: {
            stash: newlyCreatedStash,
            owningProjectId: props.task.projectId,
          },
          // not sure if this is a race due to closure...
          isOtherProjectStashed: props.currentProjectID !== newlyCreatedStash.projectId,
        });
      }
      // No need to update local sync state - will be handled by via stream pre-empting polling in the hook
      return { title: "Task synced to local filesystem", type: ToastType.SUCCESS };
    } catch (error) {
      // TODO streaming: will need to have an error message in the strea we handle instead?
      flushSync(() => setDidLastOperationFail(true));
      const errorMessage = error instanceof HTTPException ? error.detail : String(error);
      let message = `Failed to start automatic sync: ${errorMessage}`;

      // the notices will get updated by the request?
      const notices = props.task.sync?.notices;
      if (notices && notices.length > 0) {
        message += notices.length == 1 ? `\nIssue: ${notices[0]}` : `\nIssues: ${notices.join(", ")}`;
      }
      return { title: message, type: ToastType.ERROR };
    }
  };

  const stopLocalSync = async (): Promise<ToastContent> => {
    setDidLastOperationFail(false);
    wasStarting.current = false;
    try {
      const {
        data: {
          result: { actionTaken, stashFromStartOfOperation, danglingRefFromUncleanPop },
          resultingRepoInfo,
        },
      } = await disableTaskSync({
        path: { project_id: props.currentProjectID, task_id: props.task.id },
        meta: wait30sForSlowServerAction,
      });
      // TODO probably remove once migrated to streaming websocket
      // Clear local sync state
      setLocalSyncState(null);
      if (resultingRepoInfo) {
        updateLocalRepoInfo({ projectId: props.currentProjectID, repoInfo: resultingRepoInfo });
      }

      // TODO this may deserve more than a toast
      if (danglingRefFromUncleanPop) {
        return {
          type: ToastType.ERROR,
          title:
            "Task unsynced BUT an unexpected error occurred during stash restoration" +
            // TODO why don't we use description more often?
            // description:
            `Please check your local repo state. The sculptor stash commit can be found at \`${danglingRefFromUncleanPop.commitHash}\``,
        };
      }

      switch (actionTaken) {
        case LocalSyncDisabledActionTaken.STOPPED_CLEANLY:
          setSculptorStashSingletonState(null);
          // NOTE: result.danglingRefFromUncleanPop should be impossible here
          return {
            type: ToastType.SUCCESS,
            title:
              "Task unsynced from local filesystem" +
              (stashFromStartOfOperation
                ? `, pre-pair state from ${stashFromStartOfOperation.sourceBranch} restored`
                : ""),
          };
        case LocalSyncDisabledActionTaken.STOPPED_FROM_PAUSED:
          return {
            type: ToastType.WARNING,
            title:
              "Sync disabled, but local filesystem left as-is due to paused state to avoid data loss. " +
              // description:
              "The local repo state " +
              (stashFromStartOfOperation
                ? `and the pre-pair stash from \`${stashFromStartOfOperation.sourceBranch}\` `
                : "") +
              "will need to be triaged before pairing again",
          };
        case LocalSyncDisabledActionTaken.SYNC_NOT_FOUND:
          return { title: "No active sync found, but internal state should now be corrected", type: ToastType.DEFAULT };
      }
    } catch {
      // TODO streaming: will need to have an error message in the strea we handle instead?
      flushSync(() => setDidLastOperationFail(true));
      return { title: "Failed to stop Pairing Mode", type: ToastType.ERROR };
    }
  };

  const handleMainButtonClick = async (): Promise<void> => {
    if (isDisabled || isLoading) {
      return;
    }

    const toast = await (clickActionIfEnabled === "ON" ? startLocalSync() : stopLocalSync());

    props.toastCallback(toast);
  };

  const formattedSyncNotices = useMemo((): Array<string> => {
    const lines: Array<string> = [];

    const notices = props.task.sync?.notices;
    if (notices && notices.length > 0) {
      lines.push(""); // newline separator
      if (notices.length == 1) {
        lines.push(`There is a single ongoing issue:`);
      } else {
        lines.push(`There are ${notices.length} ongoing issues:`);
      }
      lines.push(...notices.map((notice) => `- ${notice.reason}`));
    }
    return lines;
  }, [props.task.sync?.notices]); // this is really the same as `props.task`, since React makes immutable updates

  const popoverContent = useMemo((): ReactElement | null => {
    const getSetupStepMessage = (step: LocalSyncSetupStep, syncBranch: string | null | undefined): string => {
      switch (step) {
        case LocalSyncSetupStep.VALIDATE_GIT_STATE_SAFETY:
          return "Validating git state";
        case LocalSyncSetupStep.MIRROR_AGENT_INTO_LOCAL_REPO:
          return `Copying agent's files to local ${syncBranch || "branch"}`;
        case LocalSyncSetupStep.BEGIN_TWO_WAY_CONTROLLED_SYNC:
          return "Starting bidirectional file sync";
        default:
          return "Setting up";
      }
    };

    const getTeardownStepMessage = (step: LocalSyncTeardownStep, originalBranch: string | null | undefined): string => {
      switch (step) {
        case LocalSyncTeardownStep.STOP_FILE_SYNC:
          return "Stopping bidirectional file sync";
        case LocalSyncTeardownStep.RESTORE_LOCAL_FILES:
          return "Restoring local files";
        case LocalSyncTeardownStep.RESTORE_ORIGINAL_BRANCH:
          return `Switching back to ${originalBranch || "original branch"}`;
        default:
          return "Cleaning up";
      }
    };

    if (shouldShowProgressTooltip) {
      const currentStep = isThisTaskStarting
        ? debouncedTransitionState?.setupStep
        : debouncedTransitionState?.teardownStep;
      const ordered_steps = isThisTaskStarting ? ORDERED_SETUP_STEPS : ORDERED_TEARDOWN_STEPS;

      // Detect if we're holding the completed state (delayed status is STARTING/STOPPING but actual is not)
      const isHoldingCompletedState =
        (debouncedTransitionState?.status === LocalSyncStatus.STARTING ||
          debouncedTransitionState?.status === LocalSyncStatus.STOPPING) &&
        props.task.sync?.status !== LocalSyncStatus.STARTING &&
        props.task.sync?.status !== LocalSyncStatus.STOPPING;

      // During hold, mark all steps as completed
      const currentStepIndex = isHoldingCompletedState
        ? ordered_steps.length
        : ordered_steps.findIndex((s) => s === currentStep);

      const branchInfo = isThisTaskStarting
        ? debouncedTransitionState?.syncBranch
        : debouncedTransitionState?.originalBranch;

      return (
        <Flex direction="column" gap="2" p="3" style={{ width: "360px" }}>
          {ordered_steps.map((step, index) => {
            const isActive = step === currentStep && !isHoldingCompletedState;
            const isCompleted = currentStepIndex !== -1 && index < currentStepIndex;
            const isLastStep = index === ordered_steps.length - 1;
            const isFailed = isLastStep && isHoldingCompletedState && didLastOperationFail;

            const stepMessage = isThisTaskStarting
              ? getSetupStepMessage(step as LocalSyncSetupStep, branchInfo)
              : getTeardownStepMessage(step as LocalSyncTeardownStep, branchInfo);

            return (
              <Flex key={step} gap="2" align="center" style={{ minWidth: 0 }}>
                <Box
                  style={{
                    width: "16px",
                    height: "16px",
                    flexShrink: 0,
                    color: isFailed ? "var(--red-9)" : isCompleted ? "var(--gold-9)" : undefined,
                  }}
                >
                  {isActive && <Spinner size="1" />}
                  {isFailed && <Cross1Icon width={16} height={16} />}
                  {isCompleted && !isFailed && <CheckIcon size={16} strokeWidth={2.5} />}
                </Box>
                <Text
                  size="2"
                  weight={isActive ? "medium" : "regular"}
                  style={{
                    color: isFailed
                      ? "var(--red-9)"
                      : isActive
                        ? "var(--gold-12)"
                        : isCompleted
                          ? "var(--gold-9)"
                          : "var(--gray-11)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    minWidth: 0,
                  }}
                >
                  {stepMessage}
                </Text>
              </Flex>
            );
          })}
        </Flex>
      );
    }

    if (shouldShowInstructionalPopover) {
      return (
        <PairingModePopoverTooltip
          task={props.task}
          repoStatus={repoStatus}
          projectID={props.currentProjectID}
          otherSyncedTaskBranchName={isOtherTaskSynced ? syncedTask.branchName : undefined}
        />
      );
    }
    return null;
  }, [
    debouncedTransitionState,
    didLastOperationFail,
    isOtherTaskSynced,
    isThisTaskStarting,
    props.currentProjectID,
    props.task,
    shouldShowInstructionalPopover,
    shouldShowProgressTooltip,
    syncedTask?.branchName,
    repoStatus,
  ]);

  const tooltipContent = useMemo((): ReactElement | null => {
    if (popoverContent !== null) {
      return null;
    }
    const tooltipLines: Array<string> = [];
    if (isDisabled) {
      tooltipLines.push(...reasonsToDisable);
    } else if (clickActionIfEnabled === "ON") {
      if (props.widgetStyle === "ICON") {
        tooltipLines.push(isOtherTaskSynced ? "Switch Pairing Mode to this Agent" : "Start Pairing Mode");
      } else {
        // TODO: Tangled mess but this logic branch is handled by the PairingModePopoverTooltip when in PRIMARY context
        return null;
      }
    } else if (props.task.sync?.status === "PAUSED") {
      tooltipLines.push(
        "Pairing Mode was paused. It will resume when problems below are addressed. You can click to stop Pairing Mode now and leave your working repository in the current state.",
      );
    } else {
      tooltipLines.push("Click to stop Pairing Mode");
    }

    tooltipLines.push(...formattedSyncNotices);
    if (tooltipLines.length > 0) {
      return (
        <>
          {tooltipLines.map((line, i) => (
            <Text key={i}>
              {line}
              <br />{" "}
            </Text>
          ))}
        </>
      );
    }
    return null;
  }, [
    popoverContent,
    clickActionIfEnabled,
    formattedSyncNotices,
    isDisabled,
    isOtherTaskSynced,
    props.task.sync?.status,
    props.widgetStyle,
    reasonsToDisable,
  ]);

  // The visual state is driven by combination of the real sync state and hover.
  let visualState = syncState;
  if (
    clickActionIfEnabled === "OFF" &&
    syncState !== LocalSyncStatus.STARTING &&
    syncState !== LocalSyncStatus.STOPPING
  ) {
    if (props.showStopWithoutHover || isMainButtonHovered) {
      visualState = "STOP_SYNCING";
    }
  }

  const buttonVisual = ((): {
    icon: ReactElement;
    text: string;
  } => {
    switch (visualState) {
      case "INACTIVE":
        return {
          icon: <ArrowUpDownIcon />,
          text: "Pairing Mode",
        };
      case "STARTING":
        return {
          icon: <Spinner size="2" />,
          text: "Starting...",
        };
      case "ACTIVE_SYNCING":
        return {
          icon: <RefreshCwIcon className={styles.syncActiveSyncingIcon} />,
          text: "Syncing...",
        };
      case "ACTIVE":
        return {
          // icon mode shows a less settled icon
          icon: <ArrowUpDownIcon />,
          text: "Pairing On",
        };
      case "PAUSED": {
        const margin = props.widgetStyle === "ICON" ? "0" : "-4px";
        return {
          icon: <CirclePauseIcon className={styles.syncPausedIcon} style={{ marginLeft: margin }} />,
          text: "Paused",
        };
      }
      case "STOPPING":
        return {
          icon: <Spinner size="2" />,
          text: "Stopping...",
        };
      case "STOP_SYNCING":
        return {
          icon: <CircleOffIcon />,
          text: "Stop Pairing",
        };
      case "ERROR":
        return {
          icon: <RefreshCwOffIcon />,
          text: "Error",
        };
    }
  })();

  const mainButtonVariant = props.widgetStyle === "ICON" && visualState === "INACTIVE" ? "soft" : "solid";

  // Convoluted but stash panel overrides other tooltip states for now.
  const isStashPanelReallyOpen = isStashPanelOpenUnlessOverridden && !shouldShowProgressTooltip;

  // Determine if we should show the stash button
  const shouldShowStashButton = isPrimaryPairingButton(props) && stashSingletonState != null;

  return (
    <PopoverTooltip
      content={popoverContent}
      defaultOpen={false}
      open={isSyncPopoverOpen}
      {...(shouldShowProgressTooltip && { align: "end" as const })}
    >
      <Flex className={styles.syncButtonContainer} data-real-sync-state={syncState} gap="2" align="center">
        <StashWarningModalProvider
          repoStatus={repoStatus}
          onProceed={handleMainButtonClick}
          localSyncStatus={syncedTask?.sync?.status}
          currentBranch={currentBranch}
        >
          {(proceedOrWarn) => (
            <PartiallyControlledTooltip
              content={tooltipContent}
              isDisabled={!canShowTooltip}
              data-testid={ElementIds.SYNC_BUTTON_TOOLTIP}
            >
              <Button
                onClick={async (e) => {
                  e.stopPropagation();
                  await proceedOrWarn();
                }}
                disabled={isDisabled}
                loading={isLoading}
                onMouseOver={() => setIsMainButtonHovered(true)}
                onMouseOut={() => setIsMainButtonHovered(false)}
                data-testid={ElementIds.SYNC_BUTTON}
                data-sync-status={props.task.sync?.status}
                style={props.widgetStyle === "REGULAR" ? { width: "110px" } : {}}
                variant={mainButtonVariant}
                className={styles.mainButton}
                data-visual-state={visualState}
                data-variant={mainButtonVariant}
                sync-status={props.task.sync?.status}
                size="1"
              >
                {buttonVisual.icon}
                {props.widgetStyle !== "ICON" && (
                  <Box width="100%">{props.widgetStyle === "REGULAR" && buttonVisual.text}</Box>
                )}
              </Button>
            </PartiallyControlledTooltip>
          )}
        </StashWarningModalProvider>
        {shouldShowStashButton && (
          <StashPanel
            currentProjectID={props.currentProjectID}
            setToast={props.toastCallback}
            syncStatus={lastStashRelevantSyncStatus.current}
            isPanelOpen={isStashPanelReallyOpen}
            setIsPanelOpen={(value) => (shouldShowProgressTooltip ? null : setIsStashPanelOpenUnlessOverridden(value))}
          />
        )}
      </Flex>
    </PopoverTooltip>
  );
};

function useDebouncedSyncState(
  syncState: LocalSyncState | undefined,
  delay: number,
  shouldHold: (prev: LocalSyncState | undefined, current: LocalSyncState | undefined) => boolean,
): LocalSyncState | undefined {
  const [heldState, setHeldState] = useState<LocalSyncState | undefined>(syncState);
  const previousRef = useRef<LocalSyncState | undefined>(syncState);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const previous = previousRef.current;
    const isHolding = timeoutRef.current !== null;

    if (isHolding) {
      const didStatusChange = previous?.status !== syncState?.status;
      if (didStatusChange) {
        clearTimeout(timeoutRef.current!);
        timeoutRef.current = null;
        setHeldState(syncState);
      }
    } else {
      if (shouldHold(previous, syncState)) {
        setHeldState(previous);
        timeoutRef.current = setTimeout(() => {
          setHeldState(syncState);
          timeoutRef.current = null;
        }, delay);
      } else {
        // normal state change
        setHeldState(syncState);
      }
    }
    previousRef.current = syncState;
  }, [syncState, delay, shouldHold]);

  useEffect(() => {
    return (): void => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
    };
  }, []);

  return heldState;
}

const PartiallyControlledTooltip = ({
  isDisabled,
  ...props
}: Omit<TooltipProps, "open" | "onOpenChange"> & { isDisabled: boolean }): ReactElement => {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  return <Tooltip {...props} open={isDisabled || !props.content ? false : isOpen} onOpenChange={setIsOpen} />;
};
