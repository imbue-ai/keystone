import {
  AlertDialog,
  Box,
  Button,
  Flex,
  Grid,
  IconButton,
  Popover,
  ScrollArea,
  Separator,
  Spinner,
  Strong,
  Text,
  TextArea,
  Tooltip,
} from "@radix-ui/themes";
import {
  ArrowDown,
  ArrowUp,
  CheckIcon,
  ChevronDown,
  ChevronRight,
  CopyIcon,
  FileTextIcon,
  InfoIcon,
  TriangleAlertIcon,
} from "lucide-react";
import type { ReactElement, SetStateAction } from "react";
import type React from "react";
import { useRef } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";

import type {
  TransferFromLocalToTaskRequest,
  TransferFromLocalToTaskResponse,
  TransferFromTaskToLocalResponse,
  TransferRepoDecision,
  TransferRepoUserChoice,
} from "~/api";
import { ElementIds } from "~/api";
import {
  type CodingAgentTaskView,
  getRepoInfo,
  type TransferFromTaskToLocalRequest,
  transferToAgent,
  transferToLocal,
} from "~/api";
import { HTTPException, RequestTimeoutError } from "~/common/Errors.ts";
import { useLocalRepoInfo } from "~/common/state/hooks/useLocalRepoInfo.ts";
import { BranchSelectorCore } from "~/components/BranchSelectorCore.tsx";
import { MarkdownBlock } from "~/components/MarkdownBlock.tsx";
import { PopoverTooltip } from "~/components/PopoverTooltip.tsx";
import type { ToastContent } from "~/components/Toast.tsx";
import { MergePanelPopoverTooltip } from "~/pages/chat/components/MergePanelPopoverTooltip.tsx";

import styles from "./SyncPanel.module.scss";

export type SyncPanelProps = {
  task: CodingAgentTaskView;
  projectID: string;

  // TODO: use context to avoid props drilling
  toastCallback?: (toast: ToastContent) => void;
  disabled?: boolean;
};

type ManualSyncNotice = {
  message: string;
  type: "success" | "warning" | "error" | "info" | "loading";

  details?: string;
  final?: true;
};

type SyncDirection = "FROM_AGENT" | "TO_AGENT";

type ManualSyncOperationStepInput = {
  operationId: number;
  stepId: number;

  userChoice: TransferRepoUserChoice | null;
};

type ManualSyncOperationStepOutput = {
  notices: Array<ManualSyncNotice>;
} & (
  | {
      outcome: "FAILURE" | "SUCCESS";
      nextStep: null;
    }
  | {
      outcome: "DECISION_NEEDED";
      nextStep: {
        operationId: number;
        stepId: number;

        decision: TransferRepoDecision;
      };
    }
);

// FIXME: this structure maintains an ongoing operation that refers to the task and project from
//       when it was created, we should abort ongoing operations if for some reason the task or
//       projectId is swapped
class ManualSyncOperation {
  readonly id: number;
  readonly direction: SyncDirection;
  readonly projectId: string;
  readonly taskId: string;
  readonly baseRequestBody: TransferFromTaskToLocalRequest;

  protected stepId: number;
  protected userChoices: Array<TransferRepoUserChoice>;

  private currentDecision: TransferRepoDecision | null;
  public notices: Array<ManualSyncNotice>;

  constructor(params: {
    id: number;
    direction: SyncDirection;
    baseRequestBody: TransferFromTaskToLocalRequest;
    projectId: string;
    taskId: string;
  }) {
    this.id = params.id;
    this.direction = params.direction;
    this.baseRequestBody = params.baseRequestBody;
    this.projectId = params.projectId;
    this.taskId = params.taskId;

    this.stepId = 0;
    // TODO: inject initial decisions for "don't ask again" functionality
    this.userChoices = [];
    this.notices = [];
    this.currentDecision = null;
  }

  protected advanceStepOrThrow(id: number, stepId: number): void {
    if (this.id !== id || this.stepId !== stepId) {
      throw new Error(
        `INTERNAL ERROR: invalid operation or its step. Received ${id}:${stepId} but expecting ${this.id}:${this.stepId}`,
      );
    }

    this.stepId += 1;
    console.log(`MANUAL SYNC: advancing the step from ${this.stepId}`);
  }

  public async initialize(): Promise<ManualSyncOperationStepOutput> {
    return await this.proceedWithDecision({
      operationId: this.id,
      stepId: 0,
      userChoice: null,
    });
  }

  // Proceed a single step of the wizard after user specified their `selection` in the `decision`. The `decision` may
  // be empty on the first request, but it would not make sense at later steps.
  public async proceedWithDecision(step: ManualSyncOperationStepInput): Promise<ManualSyncOperationStepOutput> {
    // this will lock this invocation to be the handler of this step
    // if we throw after advancing step, then we won't be able to recover the wizard
    //
    // NOTE: we can wrap this up here with a failure or propagate upwards because the problem
    //       requires user of this object to throw it away
    //
    // TODO: consider if we should be throwing for outdated callbacks or have a special outcome for them instead.
    // TODO: consider using this.currentDecision for identifying that we're in the correct step.
    const { operationId, stepId, userChoice } = step;
    this.advanceStepOrThrow(operationId, stepId);
    if (userChoice) {
      if (userChoice.decisionId != this.currentDecision?.id) {
        alert(
          `INTERNAL PROBLEM: user's choice does not match expected identifier. ${userChoice.decisionId} != ${this.currentDecision?.id}`,
        );
      }
      this.userChoices.push(userChoice);
    }

    const { outcome, nextDecision, notices } = await this.executeSingleStep();

    this.notices.push(...notices);
    if (outcome === "DECISION_NEEDED") {
      console.log("storing the nextDecision for later", nextDecision);
      this.currentDecision = nextDecision;
      return {
        outcome: "DECISION_NEEDED",
        notices: notices,
        nextStep: {
          operationId: this.id,
          stepId: this.stepId,
          decision: nextDecision,
        },
      };
    }

    return {
      outcome: outcome,
      notices: notices,
      nextStep: null,
    };
  }

  protected async executeSingleStep(): Promise<
    | {
        outcome: "DECISION_NEEDED";
        nextDecision: TransferRepoDecision;
        notices: Array<ManualSyncNotice>;
      }
    | {
        outcome: "FAILURE" | "SUCCESS";
        nextDecision: null;
        notices: Array<ManualSyncNotice>;
      }
  > {
    const requestBody: TransferFromTaskToLocalRequest & TransferFromLocalToTaskRequest = {
      ...this.baseRequestBody,
      userChoices: [...(this.baseRequestBody.userChoices ?? []), ...this.userChoices],
    };

    const backendAPI = this.direction === "TO_AGENT" ? transferToAgent : transferToLocal;

    let data: (TransferFromTaskToLocalResponse & TransferFromLocalToTaskResponse) | undefined;
    try {
      ({ data } = await backendAPI({
        path: { project_id: this.projectId, task_id: this.taskId },
        body: requestBody,
        meta: {
          skipWsAck: true,
          timeout: 30000,
        },
      }));
    } catch (error) {
      let errorDetail: string | undefined = error instanceof Error ? error.message : `${error}`;
      let errorMessage: string = `Unexpected error: ${typeof error}.`;
      if (error instanceof HTTPException) {
        errorMessage = `Error ${error.status} during synchronization process.`;
      } else if (error instanceof RequestTimeoutError) {
        errorMessage = `Timeout while awaiting for the operation to finish.`;
        errorDetail = undefined;
      } else if (error instanceof Error) {
        errorMessage = `Error during synchronization process: ${error.name}`;
      }
      return {
        outcome: "FAILURE",
        nextDecision: null,
        notices: [
          {
            message: errorMessage,
            details: errorDetail,
            type: "error",
            final: true,
          },
        ],
      };
    }

    const notices =
      data.notices?.map((notice) => ({
        message: notice.message,
        type: (notice.kind?.toLowerCase() as ManualSyncNotice["type"]) ?? "info",
        details: notice.details ?? undefined,
      })) ?? [];

    if (data.missingDecisions && data.missingDecisions.length > 0) {
      return {
        outcome: "DECISION_NEEDED",
        nextDecision: data.missingDecisions[0],
        notices: notices,
      };
    } else {
      return {
        outcome: data.success ? "SUCCESS" : "FAILURE",
        nextDecision: null,
        notices: [
          ...notices,
          {
            message: `Finished ${data.success ? "successfully" : "with an error"}`,
            type: data.success ? "success" : "error",
            final: true,
          },
        ],
      };
    }
  }
}

export const SyncPanel = ({ task, projectID }: SyncPanelProps): ReactElement => {
  const [recentBranches, setRecentBranches] = useState<Array<string> | null>(null);
  const [isInProgress, setIsInProgress] = useState<boolean>(false);
  const [isPanelOpen, setIsPanelOpen] = useState<boolean>(false);

  // lifted from ManualSyncNotice
  // FIXME: push frontend state to jotai, create backend state for merge operations to enable streaming of notices
  const [notices, setNotices] = useState<Array<ManualSyncNotice>>([]);
  const [selectedLocalBranch, setSelectedLocalBranch] = useState<string>(task.sourceBranch);

  const isRepoInfoFetching = useRef<boolean>(false);
  const fetchRepoInfo = useCallback(async () => {
    if (isRepoInfoFetching.current) {
      console.log("MANUAL SYNC: Skipping repo info fetching: already in progress");
      return;
    }

    isRepoInfoFetching.current = true;
    try {
      const { data } = await getRepoInfo({
        path: { project_id: projectID },
        meta: {
          skipWsAck: true,
        },
      });
      if (data) {
        setRecentBranches(data.recentBranches);
      }
    } catch (error) {
      console.error("Error fetching user repo info:", error);
      return;
    } finally {
      isRepoInfoFetching.current = false;
    }
  }, [projectID]);

  useEffect(() => {
    setNotices([]);

    // TODO(PROD-2359): either use a logical identifier to make it possible
    //  for the user to default to "current" or "agent's mirror", or keep
    //  each task's default selection persistent, or all of them behind settings
    setSelectedLocalBranch(task.sourceBranch);
  }, [task.id, task.sourceBranch]);

  // TODO: use an event-based provider of recent branches
  useEffect(() => {
    fetchRepoInfo().finally(() => {});
    const intervalId = setInterval(fetchRepoInfo, 10000);
    return (): void => {
      clearInterval(intervalId);
    };
  }, [fetchRepoInfo]);

  const isDisabled = task.sync.status !== "INACTIVE";
  const tooltipWhenDisabled = (
    <Text>
      <Strong>Disabled</Strong> while
      <br /> Pairing Mode is active
    </Text>
  );

  return (
    <Popover.Root open={isPanelOpen || isInProgress} onOpenChange={setIsPanelOpen}>
      {/* Once we find a common design for all the tooltips, all the tooltip logic will be pushed into the PopoverTooltip below. */}
      <Tooltip content={tooltipWhenDisabled} open={isDisabled ? undefined : false}>
        <PopoverTooltip content={<MergePanelPopoverTooltip />} open={isPanelOpen || isDisabled ? false : undefined}>
          <Popover.Trigger>
            <Button size="1" disabled={isDisabled} data-testid={ElementIds.MERGE_PANEL_BUTTON}>
              Merge
              <ChevronDown />
            </Button>
          </Popover.Trigger>
        </PopoverTooltip>
      </Tooltip>
      <Popover.Content data-testid={ElementIds.MERGE_PANEL_CONTENT} className={styles.panel} data-disabled={isDisabled}>
        {isDisabled && (
          <Tooltip content={tooltipWhenDisabled} side="left" delayDuration={0}>
            <Box className={styles.panelOverlay}></Box>
          </Tooltip>
        )}
        <ManualSyncPanel
          task={task}
          projectID={projectID}
          recentBranches={recentBranches}
          setIsInProgress={setIsInProgress}
          setNotices={setNotices}
          selectedLocalBranch={selectedLocalBranch}
          setSelectedLocalBranch={setSelectedLocalBranch}
          fetchRepoInfo={fetchRepoInfo}
        />
        {notices && notices.length > 0 && (
          <>
            <Separator size="4" className={styles.separator} />
            <NoticesList notices={notices} />
          </>
        )}
      </Popover.Content>
    </Popover.Root>
  );
};

type PendingTooltipButtonProps = React.ComponentPropsWithoutRef<typeof Button> & {
  onClick: () => Promise<void>;
  tooltip?: string;
};

const PendingTooltipButton = (props: PendingTooltipButtonProps): ReactElement => {
  const [isHandlerActive, setIsHandlerActive] = useState(false);

  const handleClick = async (): Promise<void> => {
    if (props.disabled || props.loading || isHandlerActive) return;

    setIsHandlerActive(true);
    try {
      await props.onClick();
    } finally {
      setIsHandlerActive(false);
    }
  };

  const isLoading = isHandlerActive || props.loading;

  return (
    <>
      <Tooltip content={props.tooltip} open={props.tooltip ? undefined : false} delayDuration={700}>
        <Button {...props} disabled={isLoading || props.disabled} loading={false} onClick={handleClick}>
          {props.children}
        </Button>
      </Tooltip>
    </>
  );
};

type ManualSyncPanelProps = {
  task: CodingAgentTaskView;
  projectID: string;

  recentBranches: Array<string> | null;
  toastCallback?: (toast: ToastContent) => void;

  setIsInProgress: (value: boolean) => void;
  setNotices: (_: SetStateAction<Array<ManualSyncNotice>>) => void;

  selectedLocalBranch: string;
  setSelectedLocalBranch: (selectedLocalBranch: string) => void;

  fetchRepoInfo: () => Promise<void>;
};

const ManualSyncPanel = (props: ManualSyncPanelProps): ReactElement => {
  const [activeDialog, setActiveDialog] = useState<DialogWithOptions | null>(null);

  const localRepoInfo = useLocalRepoInfo(props.projectID);

  if (!props.task.branchName) {
    throw new Error("Task has no branch name set.");
  }

  const [inProgress, setInProgress] = useState<SyncDirection | null>(null);
  // HACK: propagating the current progress of the operation so that we don't get unmounted (easily)
  props.setIsInProgress(inProgress !== null);

  const { selectedLocalBranch, setSelectedLocalBranch } = props;
  const setNotices = props.setNotices;

  // this reference is only used in three functions:
  // - the handler of manual sync operation, to start the operation
  // - the executeManualSyncOperationStep, to react to users input on a dialog
  // - the useEffect that cancels ongoing operations
  // it should not be read nor modified in any other place
  const activeOperation = useRef<ManualSyncOperation | null>(null);
  const activeOperationID = useRef(0);

  const clearOperation = useCallback(
    (operationId: number) => {
      if (activeOperationID.current != operationId) {
        console.error(
          `MANUAL SYNC: Not clearing manual sync operation, id mismatch; got ${operationId} currently ${activeOperationID.current}`,
        );
        return;
      }

      console.log(`MANUAL SYNC: clearing operation ${operationId}`);

      // TODO: route directly through a global object, so that the activeOperation is marked as dead.
      activeOperation.current = null;

      setInProgress(null);
      setActiveDialog(null);
    },
    [setActiveDialog, setInProgress],
  );

  useEffect(() => {
    const op = activeOperation.current;

    // TODO(PROD-2359): don't cancel the operation, continue running
    //   it in the background and only cancel on dialog. Store notices
    //   for later.
    // Clear the state if we're switching the task and there were no other races involved.
    if (inProgress !== null && op && op.taskId != props.task.id) {
      console.log("MANUAL SYNC: clearing operation due to task switching");
      clearOperation(op.id);
    }
  }, [props.task.id, props.setIsInProgress, inProgress, clearOperation]);

  const processOperationStep = useCallback(
    async (operationId: number, operation: Promise<ManualSyncOperationStepOutput>): Promise<void> => {
      if (operationId != activeOperationID.current) {
        // this may happen if the user clicks away to another task while we are handling another user's action (click on a dialog, initial click on the button).
        console.warn(
          `MANUAL SYNC: received a callback for the wrong operation: ${operationId} != ${activeOperationID.current}`,
        );
        return;
      }

      let result: ManualSyncOperationStepOutput | null = null;
      try {
        result = await operation;
      } catch (error) {
        console.log("MANUAL SYNC: operation failed with an error", error);
        clearOperation(operationId);
        return;
      }

      if (activeOperation.current?.id !== operationId) {
        // this may happen if the user clicks away while we were waiting for the backend to resolve the previous request (likely the initial one, as otherwise the modal would still be hanging).
        console.warn(`MANUAL SYNC: operation changed while backend was processing the event ${operationId}`);
        return;
      }

      const { outcome, nextStep, notices } = result;
      setNotices(notices);

      if (outcome !== "DECISION_NEEDED") {
        console.log("MANUAL SYNC: sync operation is finished, no more decisions or it failed");
        clearOperation(operationId);
        return;
      }

      if (!nextStep) {
        console.error(`MANUAL SYNC: decision is needed but none provided, outcome: ${outcome}`);
        clearOperation(operationId);
        return;
      }

      if (nextStep.operationId != activeOperationID.current) {
        alert(
          `INTERNAL ERROR: mismatched identifier between the sync operation and the callback ${nextStep.operationId} != ${activeOperationID.current}; concurrent sync operation started before the previous one finished.`,
        );
        clearOperation(operationId);
        return;
      }

      const decision = nextStep.decision;
      setActiveDialog({
        operationId: nextStep.operationId,
        stepId: nextStep.stepId,

        // TODO: can be pushed to the external object now that it has stepId and operationId
        backendDecisionId: decision.id,

        title: decision.title,
        message: decision.message,
        detailedContext: decision.detailedContext
          ? {
              title: decision.detailedContextTitle ?? "details available",
              content: decision.detailedContext,
            }
          : undefined,

        options: decision.options.map((option) => ({
          id: option.option,
          text: option.option,
          isDestructive: option.isDestructive,
          isDefault: option.isDefault,
          tooltip: "Click to proceed",
        })),

        hideCancel: false,
        isCommitMessageRequired: decision.isCommitMessageRequired,
      });
    },
    [setActiveDialog, clearOperation, setNotices],
  );

  const handleWizardOptionSelected = useCallback(
    async (dialog: DialogWithOptions, selected: DialogOption, commitMessage?: string): Promise<void> => {
      console.log(
        `MANUAL SYNC: committing user's decision into the operation: ${dialog.operationId}-${dialog.stepId}-${dialog.backendDecisionId} -> ${selected.id}`,
      );

      if (!activeOperation.current) {
        alert("INTERNAL ERROR: No active manual sync operation in progress!");
        return;
      }

      if (dialog.operationId != activeOperationID.current) {
        alert(
          `INTERNAL ERROR: received a dialog callback for an outdated operation ${dialog.operationId} != ${activeOperationID.current}`,
        );
      }

      const resultPromise = activeOperation.current.proceedWithDecision({
        operationId: dialog.operationId,
        stepId: dialog.stepId,

        userChoice: {
          decisionId: dialog.backendDecisionId,
          choice: selected.id,
          commitMessage: commitMessage,
        },
      });
      await processOperationStep(dialog.operationId, resultPromise);
    },
    [processOperationStep],
  );

  const handleWizardCancel = useCallback(
    async (dialog: DialogWithOptions): Promise<void> => {
      console.log("MANUAL SYNC: sync cancelled");
      clearOperation(dialog.operationId);

      setNotices((ns) => [
        ...ns,
        {
          type: "warning",
          // it's only cancelled if there was an alternative
          message: dialog.options.length > 0 ? "Operation cancelled." : "Operation finished.",
        },
      ]);
    },
    [clearOperation, setNotices],
  );

  // This is the start of a manual sync operation, it will initialize the internal state and trigger the first backend request.
  const handleStartSync = useCallback(
    async (direction: SyncDirection): Promise<void> => {
      // these shouldn't happen if all is rendered correctly
      // throwing an alert for now to make a louder noise when they happen
      if (activeOperation.current) {
        alert("INTERNAL PROBLEM: Button clicked while operation is in progress! (ref check)");
        return;
      }

      if (inProgress) {
        // TODO: override the state, ref actually owns it?
        alert("INTERNAL PROBLEM: Button clicked while operation is in progress! (state check)");
        return;
      }

      if (!selectedLocalBranch) {
        alert("INTERNAL PROBLEM: local branch is not selected");
        return;
      }

      if (!localRepoInfo?.currentBranch) {
        alert("INTERNAL PROBLEM: can't start syncing without knowing what the current branch is");
        return;
      }

      setNotices([
        {
          type: "loading",
          message: "Working...",
        },
      ]);
      setInProgress(direction);

      // Setting the initial state, this is the only place where this should happen.
      // If any of the assumptions or parameters change, then the whole operation should be
      // cancelled and tried from scratch.
      // TODO: move ref management to a global object, the constructor becomes a method on that object and only returns the operation identifier
      activeOperationID.current += 1;
      const operationID = activeOperationID.current;
      console.log(`MANUAL SYNC: advancing the current operation to ${operationID}`);

      activeOperation.current = new ManualSyncOperation({
        id: operationID,
        projectId: props.projectID,
        taskId: props.task.id,

        direction: direction,
        baseRequestBody: {
          decisions: [],
          assumptions: {
            localBranch: localRepoInfo.currentBranch,
            agentRepoIsClean: true, // TODO: fix or remove
          },
          includeUncommittedChanges: false, // TODO: implement with a checkbox
          targetLocalBranch: selectedLocalBranch,
        },
      });

      await processOperationStep(operationID, activeOperation.current.initialize());
    },
    [
      inProgress,
      processOperationStep,
      props.projectID,
      props.task.id,
      selectedLocalBranch,
      setNotices,
      localRepoInfo?.currentBranch,
    ],
  );

  const reposState = {
    agentBranchName: props.task.branchName,
    localBranchName: localRepoInfo?.currentBranch || null,
  };

  const loadingTooltip = ((): string | null => {
    if (inProgress) {
      return "Operation is in progress...";
    }

    if (props.recentBranches === null) {
      return "Loading information about your local repository";
    }
    return null;
  })();

  const visualState = {
    disabled: props.task.sync.status !== "INACTIVE",
    disabledTooltip: (
      <Text>
        Disable automatic sync
        <br />
        to control the Manual Sync.
      </Text>
    ),
  };

  const buttonsState = {
    fromAgent: {
      disabled: visualState.disabled || !!inProgress || props.recentBranches === null,
      loading: inProgress === "FROM_AGENT",
      tooltip: loadingTooltip ?? "Pull changes from Agent to your Local branch",
    },
    toAgent: {
      disabled: visualState.disabled || !!inProgress || props.recentBranches === null,
      loading: inProgress === "TO_AGENT",
      tooltip: loadingTooltip ?? "Push Local branch into to the Agent",
    },
  };

  const isSelectedLocalBranchCurrent = selectedLocalBranch === reposState.localBranchName;

  return (
    <>
      <Box p="4">
        <Text size="3" weight="medium" className={styles.panelTitle}>
          Merge changes
        </Text>
        <Grid columns="55px 295px" gap="3" mt="3">
          <Box>
            <Text className={styles.panelText} weight="medium">
              Agent
            </Text>
          </Box>
          <Box className={styles.agentBranchName}>
            <Tooltip content={`started from ${props.task.sourceBranch}`} delayDuration={700}>
              <Text ml="1">{reposState.agentBranchName}</Text>
            </Tooltip>
          </Box>
          <Flex gap="1" pr="1" flexGrow="1" style={{ gridColumn: "2 / 3" }}>
            <PendingTooltipButton
              onClick={async () => {
                await handleStartSync("FROM_AGENT");
              }}
              {...buttonsState.fromAgent}
              size="1"
              style={{ width: "50%" }}
              data-testid={ElementIds.MERGE_PANEL_PULL_OR_FETCH_BUTTON}
            >
              <Spinner loading={buttonsState.fromAgent.loading}>
                <ArrowDown />
              </Spinner>
              {isSelectedLocalBranchCurrent ? "Pull" : "Fetch"}
              {buttonsState.fromAgent.loading ? "ing..." : ""}
            </PendingTooltipButton>
            <PendingTooltipButton
              onClick={async () => {
                await handleStartSync("TO_AGENT");
              }}
              {...buttonsState.toAgent}
              size="1"
              style={{ width: "50%" }}
              data-testid={ElementIds.MERGE_PANEL_PUSH_BUTTON}
            >
              <Spinner loading={buttonsState.toAgent.loading}>
                <ArrowUp />
              </Spinner>
              {buttonsState.toAgent.loading ? "Pushing..." : "Push"}
            </PendingTooltipButton>
          </Flex>
          <Box>
            <Text className={styles.panelText} weight="medium">
              Local
            </Text>
          </Box>
          <Box>
            <Spinner
              size="1"
              style={{ verticalAlign: "middle" }}
              loading={reposState.localBranchName === null || reposState.agentBranchName === null}
            >
              <BranchSelector
                task={props.task}
                projectID={props.projectID}
                selectedBranch={selectedLocalBranch}
                onSelectedBranchChanged={setSelectedLocalBranch}
                recentBranches={props.recentBranches ?? []}
                disabled={!!inProgress}
                fetchRepoInfo={props.fetchRepoInfo}
              />
              {isSelectedLocalBranchCurrent && (
                <Box mt="2">
                  <Text size="2" className={styles.panelNotice}>
                    This is your current locally checked out branch.
                  </Text>
                </Box>
              )}
            </Spinner>
          </Box>
          <Flex style={{ gridColumn: "2 / 3" }} className={styles.panelNotice}>
            <Text size="2">
              Use the buttons to perform git operations between branches of your local repo and the branch inside the
              Agent&apos;s container.
            </Text>
          </Flex>
          {/*
          <Flex style={{ gridColumn: "2 / 3" }} gapY="1" direction="column" mt="2">
            <Skeleton>
              <Text size="1">A</Text>
            </Skeleton>
            <Skeleton width="85%">
              <Text size="1">A</Text>
            </Skeleton>
          </Flex>
          */}
        </Grid>
      </Box>

      {activeDialog && (
        <AlertDialogWithOptions
          dialog={activeDialog}
          onCancel={handleWizardCancel}
          onOptionSelected={handleWizardOptionSelected}
        />
      )}
    </>
  );
};

const NoticesList = (props: { notices: Array<ManualSyncNotice> }): ReactElement => {
  return (
    <Flex
      className={styles.footerNoticeSection}
      p="5"
      gapY="4"
      direction="column"
      data-testid={ElementIds.MERGE_PANEL_FOOTER_NOTICES}
      role="list"
    >
      {props.notices.map((notice, i) => (
        <ExpandableNotice key={i} notice={notice} />
      ))}
    </Flex>
  );
};

const ExpandableNotice = (props: { notice: ManualSyncNotice }): ReactElement => {
  const [isExpanded, setIsExpanded] = useState(false);

  const color: "green" | "red" | undefined = useMemo((): "green" | "red" | undefined => {
    if (!props.notice.final) {
      return undefined;
    }

    if (props.notice.type === "success") {
      return "green";
    }

    if (props.notice.type === "error") {
      return "red";
    }
  }, [props.notice.type, props.notice.final]);

  const icon = useMemo((): ReactElement => {
    switch (props.notice.type) {
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
  }, [props.notice.type, color]);

  return (
    <Flex gapX="3" align="start">
      <Flex align="start" pt="3px">
        {icon}
      </Flex>
      <Flex direction="row" align="start" style={{ flex: 1 }}>
        <Text size="2" role="listitem" className={styles.footerNotice} data-notice-type={props.notice.type}>
          <Text color={color}>{props.notice.message}</Text>
          {props.notice.details && (
            <Popover.Root open={isExpanded} onOpenChange={setIsExpanded}>
              <Popover.Trigger>
                <Button variant="ghost" size="1" mt="2px" style={{ padding: "4px", marginLeft: "2px" }}>
                  {isExpanded && <ChevronDown />}
                  {!isExpanded && <ChevronRight />}
                </Button>
              </Popover.Trigger>
              <Popover.Content style={{ padding: 0 }}>
                <PreformattedTextView content={props.notice.details} />
              </Popover.Content>
            </Popover.Root>
          )}
        </Text>
      </Flex>
    </Flex>
  );
};

type DialogOption = {
  id: string;

  text: string;
  isDestructive?: boolean;
  isDefault?: boolean;
  tooltip?: string;
};

type DialogWithOptions = {
  operationId: number;
  stepId: number;

  backendDecisionId: string;

  title: string;
  message: string;
  detailedContext?: {
    title: string;
    content: string;
  };

  options: Array<DialogOption>;
  hideCancel?: boolean;
  isCommitMessageRequired?: boolean;
};

type AlertDialogWithOptionsProps = {
  open?: boolean;
  dialog: DialogWithOptions;

  onOptionSelected: (dialog: DialogWithOptions, option: DialogOption, commitMessage?: string) => Promise<void>;
  onCancel: (dialog: DialogWithOptions) => Promise<void>;
};

const AlertDialogWithOptions = (props: AlertDialogWithOptionsProps): ReactElement => {
  // TODO: persist all past selections per dialog step, to show a visual stack
  const [lastSelected, setLastSelected] = useState<{
    operationId: number;
    stepId: number;

    optionId: string | null;
  } | null>(null);

  // FIXME: wire up the commit message to a persistent per-task global source
  const [commitMessage, setCommitMessage] = useState("");

  const { onCancel, onOptionSelected } = props;

  const isCurrentDialogDisabled =
    lastSelected?.operationId === props.dialog.operationId && lastSelected?.stepId == props.dialog.stepId;
  const currentDialogSelectedOption = lastSelected?.optionId;

  const handleCancel = useCallback(async () => {
    setLastSelected({
      operationId: props.dialog.operationId,
      stepId: props.dialog.stepId,
      optionId: null,
    });
    await onCancel(props.dialog);
  }, [onCancel, props.dialog, setLastSelected]);

  const handleOptionSelected = useCallback(
    async (option: DialogOption) => {
      setLastSelected({
        operationId: props.dialog.operationId,
        stepId: props.dialog.stepId,
        optionId: option.id,
      });

      await onOptionSelected(props.dialog, option, commitMessage);
    },
    [onOptionSelected, props.dialog, setLastSelected, commitMessage],
  );

  const isDefaultOptionDisabled = props.dialog.isCommitMessageRequired && commitMessage.trim() === "";

  return (
    <AlertDialog.Root
      open={props.open ?? true}
      /* TODO: implement `onOpenChange` that plays well with the cancel.
         Currently default keyboard interactions are not going to close the dialog.

         The `onOpenChange` handler would have to distinguish between user dismissing the
         dialog and the dialog getting closed because of a button click. Maybe if we
         prevent the propagation of button clicks the cancel will only be triggered
         by other actions?
       */
    >
      <AlertDialog.Content data-dialog-id={props.dialog.backendDecisionId}>
        <AlertDialog.Title>
          <Flex width="100%">
            <Box>{props.dialog.title}</Box>
          </Flex>
        </AlertDialog.Title>
        <MarkdownBlock content={props.dialog.message} />
        {props.dialog.detailedContext && (
          <Box ml="auto">
            <Popover.Root>
              <Popover.Trigger>
                <Button variant="soft" size="1" m="0" style={{ maxWidth: "70%" }}>
                  <Flex gapX="1" width="100%">
                    <FileTextIcon />
                    <Text style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {props.dialog.detailedContext.title}
                    </Text>
                  </Flex>
                </Button>
              </Popover.Trigger>
              <Popover.Content style={{ padding: 0 }}>
                <PreformattedTextView content={props.dialog.detailedContext.content} />
              </Popover.Content>
            </Popover.Root>
          </Box>
        )}
        {props.dialog.isCommitMessageRequired && (
          // FIXME: re-use the part of CommitChanges so that we have consistent UX and validation
          <Flex mt="2" direction="column">
            <TextArea
              placeholder="Enter commit message..."
              value={commitMessage}
              onChange={(e) => setCommitMessage(e.target.value)}
              style={{ minHeight: "4em" }}
              disabled={isCurrentDialogDisabled}
              data-testid={ElementIds.MERGE_PANEL_DIALOG_COMMIT_MESSAGE_INPUT}
            />
          </Flex>
        )}

        <Flex gap="3" mt="4" justify="end">
          {!props.dialog.hideCancel && (
            <AlertDialog.Cancel>
              <Button
                variant="soft"
                onClick={handleCancel}
                disabled={isCurrentDialogDisabled}
                loading={isCurrentDialogDisabled && currentDialogSelectedOption === null}
              >
                {props.dialog.options.length > 0 ? "Cancel" : "Ok"}
              </Button>
            </AlertDialog.Cancel>
          )}
          {props.dialog.options.map((option, index) => (
            <AlertDialog.Action key={`${index}-${option.id}`}>
              <Button
                variant={option.isDefault || option.isDestructive ? "solid" : "soft"}
                color={option.isDestructive ? "red" : undefined}
                onClick={async () => handleOptionSelected(option)}
                disabled={isCurrentDialogDisabled || (option.isDefault && isDefaultOptionDisabled)}
                loading={isCurrentDialogDisabled && currentDialogSelectedOption === option.id}
              >
                {option.text}
              </Button>
            </AlertDialog.Action>
          ))}
        </Flex>
      </AlertDialog.Content>
    </AlertDialog.Root>
  );
};

type BranchSelectorProps = {
  task: CodingAgentTaskView;
  projectID: string;

  selectedBranch: string;
  onSelectedBranchChanged: (selectedBranch: string) => void;

  recentBranches: Array<string>;
  disabled?: boolean;
  fetchRepoInfo: () => Promise<void>;
};

const BranchSelector = (props: BranchSelectorProps): ReactElement => {
  const localRepoInfo = useLocalRepoInfo(props.projectID);

  const branches = getRelevantBranchesWithBadges(props.task, props.recentBranches, localRepoInfo?.currentBranch);

  return (
    <BranchSelectorCore
      selectedBranch={props.selectedBranch}
      onBranchSelected={props.onSelectedBranchChanged}
      branches={branches}
      triggerContent={
        <Flex gapX="2" className={styles.localBranchNameSelector}>
          <Text>{props.selectedBranch}</Text>
        </Flex>
      }
      disabled={props.disabled}
      testId={ElementIds.MERGE_PANEL_BRANCH_SELECTOR}
      contentTestId={ElementIds.MERGE_PANEL_BRANCH_OPTIONS}
      height={350}
      onOpenChange={(open) => {
        if (open) {
          props.fetchRepoInfo();
        }
      }}
    />
  );
};

const PreformattedTextView = (props: { content: string }): ReactElement => {
  const handleCopyText = (): void => {
    navigator.clipboard.writeText(props.content);
    // FIXME: toastCallback from a context, not gonna drill it down 4 layers to here
  };

  return (
    <Flex className={styles.textViewContainer}>
      <IconButton variant="ghost" onClick={handleCopyText}>
        <CopyIcon />
      </IconButton>
      <ScrollArea>
        <pre>{props.content}</pre>
      </ScrollArea>
    </Flex>
  );
};

const getRelevantBranchesWithBadges = (
  task: CodingAgentTaskView,
  recentBranches: Array<string>,
  currentBranch?: string,
): Array<{ branch: string; badges: Array<string> }> => {
  const branches = new Map<string, Array<string>>();

  const addBranchIfValid = (branch?: string | null, badge?: string): void => {
    if (branch) {
      const badges = branches.get(branch) ?? [];
      branches.set(branch, badge ? [...badges, badge] : badges);
    }
  };

  addBranchIfValid(currentBranch, "current");
  addBranchIfValid(task.sourceBranch, "base");
  addBranchIfValid(task.branchName, "agent's mirror");

  recentBranches?.forEach((branch) => addBranchIfValid(branch));

  return Array.from(branches).map(([branch, badges]) => {
    return {
      branch: branch,
      badges: badges,
    };
  });
};
