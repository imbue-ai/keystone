import { CheckIcon, ChevronDownIcon } from "@radix-ui/react-icons";
import { Badge, Box, DropdownMenu, Flex, IconButton, Spinner, Text, Tooltip } from "@radix-ui/themes";
import { useAtom } from "jotai";
import { atomFamily, atomWithStorage } from "jotai/utils";
import { Play, Repeat } from "lucide-react";
import type { ReactElement } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useTaskChatMessages, useTaskDetailWithDefaults } from "~/common/state/hooks/useTaskDetail.ts";

import type { ChatMessage, ScoreMessage, StatusMessage } from "../../../api/index.ts";
import { ArtifactType, ChatMessageRole, sendMessageGeneric } from "../../../api/index.ts";
import { useTaskPageParams } from "../../../common/NavigateUtils.ts";
import { useTask } from "../../../common/state/hooks/useTaskHelpers.ts";
import { Toast, type ToastContent, ToastType } from "../../../components/Toast.tsx";
import type { NewScoutOutputsData, ScoutOutputWithSource } from "../Types.ts";
import { CheckStatusDisplay, getCheckStatusDisplay } from "../utils/checkStatusUtils.ts";
import { filterScoutOutputsFromCheckOutputs } from "./artifacts/scoutUtils.ts";
import { extractUserMessageIds } from "./artifacts/suggestionUtils.ts";
import { useUserMessageIds } from "./artifacts/useSuggestions.ts";
import styles from "./ScoutPanel.module.scss";

type ScoutStatus = "blank" | "running" | "complete";
type ScoutStatusData = {
  status: ScoutStatus;
  score?: number;
};

const getScoutStatusFromOutputs = (scoutOutputs: Array<ScoutOutputWithSource>): ScoutStatusData => {
  if (scoutOutputs.length === 0) {
    return { status: "blank" };
  }
  // Find the latest ScoreMessage to get the overall_score
  let latestScore: number | undefined;
  for (let i = scoutOutputs.length - 1; i >= 0; i--) {
    const output = scoutOutputs[i].output;
    if (output.data.objectType === "ScoreMessage") {
      latestScore = (output.data as ScoreMessage).overallScore;
      break;
    }
  }
  // Check if we have a completed status
  const hasCompleteStatus = scoutOutputs.some(
    (output) =>
      output.output.data.objectType === "StatusMessage" && (output.output.data as StatusMessage).status === "completed",
  );
  if (hasCompleteStatus && latestScore !== undefined) {
    return { status: "complete", score: latestScore };
  }
  return { status: "blank" };
};

const getScoreColor = (score: number): string => {
  if (score < 0.4) {
    return "var(--red-9)";
  } else if (score < 0.95) {
    return "var(--orange-9)";
  } else {
    return "var(--green-9)";
  }
};

const useMostRecentScoutOutputs = (
  scoutOutputsData: NewScoutOutputsData | undefined,
  chatMessages: Array<ChatMessage>,
): Array<ScoutOutputWithSource> => {
  return useMemo(() => {
    const userMessageIds = extractUserMessageIds(chatMessages);
    if (userMessageIds.length === 0) {
      return [];
    }
    const mostRecentMessageId = userMessageIds[userMessageIds.length - 1];
    return scoutOutputsData?.scoutOutputsByMessageId[mostRecentMessageId] || [];
  }, [scoutOutputsData, chatMessages]);
};

// Atom family to store auto-run preference per task
const scoutAutoRunAtomFamily = atomFamily((taskId: string) =>
  atomWithStorage<boolean>(`scout_auto_run_${taskId}`, false),
);

const useScoutAutoRun = (
  taskId: string,
  onRunScout: () => void,
): { isAutoRunEnabled: boolean; setIsAutoRunEnabled: (isAutoRunEnabled: boolean) => void } => {
  const [isAutoRunEnabled, setIsAutoRunEnabled] = useAtom(scoutAutoRunAtomFamily(taskId));
  const task = useTask(taskId);
  const { chatMessages } = useTaskChatMessages(taskId);

  // Track the last processed message ID to avoid duplicate runs
  const lastProcessedMessageIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!isAutoRunEnabled || !task || task.status === "RUNNING") {
      return;
    }

    // Get the last message
    const lastMessage = chatMessages?.[chatMessages.length - 1];

    // Only run if the last message is from the assistant
    if (lastMessage?.role !== ChatMessageRole.ASSISTANT) {
      return;
    }

    // Check if we've already processed this message
    if (lastProcessedMessageIdRef.current === lastMessage.id) {
      return;
    }

    // Update the ref and trigger the run
    lastProcessedMessageIdRef.current = lastMessage.id;
    onRunScout();
  }, [isAutoRunEnabled, task, chatMessages, onRunScout]);

  return {
    isAutoRunEnabled,
    setIsAutoRunEnabled,
  };
};

export const ScoutPanel = (): ReactElement | null => {
  const { projectID, taskID } = useTaskPageParams();
  const { artifacts, checksData } = useTaskDetailWithDefaults(taskID ?? "");
  const { chatMessages } = useTaskChatMessages(taskID ?? "");
  const [toast, setToast] = useState<ToastContent | null>(null);
  const [isRequestInFlight, setIsRequestInFlight] = useState(false);
  const task = useTask(taskID ?? "");

  const userMessageIds = useUserMessageIds(chatMessages);
  const allRunsScoutOutputsData = filterScoutOutputsFromCheckOutputs(artifacts[ArtifactType.NEW_CHECK_OUTPUTS]);
  const allScoutOutputs = useMostRecentScoutOutputs(allRunsScoutOutputsData, chatMessages ?? []);
  const scoutStatusData = getScoutStatusFromOutputs(allScoutOutputs);

  const handleRunScout = async (): Promise<void> => {
    if (!projectID || !taskID) return;

    const latestUserMessageId = userMessageIds[userMessageIds.length - 1];
    if (!latestUserMessageId) return;
    setIsRequestInFlight(true);
    try {
      await sendMessageGeneric({
        path: { project_id: projectID, task_id: taskID },
        body: {
          message: {
            object_type: "RestartCheckUserMessage",
            check_name: "Scout",
            user_message_id: latestUserMessageId,
          },
          is_awaited: false,
        },
      });
    } catch (error) {
      console.error("Failed to run Scout:", error);
      setToast({ title: "Failed to run Scout", type: ToastType.ERROR });
    } finally {
      setIsRequestInFlight(false);
    }
  };

  const { isAutoRunEnabled, setIsAutoRunEnabled } = useScoutAutoRun(taskID ?? "", handleRunScout);

  const handleStopScout = async (e: React.MouseEvent): Promise<void> => {
    e.stopPropagation();
    if (!projectID || !taskID || runningChecks.length === 0) return;

    setIsRequestInFlight(true);
    try {
      await Promise.all(
        runningChecks.map((check) =>
          sendMessageGeneric({
            path: { project_id: projectID, task_id: taskID },
            body: {
              message: {
                object_type: "StopCheckUserMessage",
                check_name: "Scout",
                run_id: check.runId,
                user_message_id: check.userMessageId,
              },
              is_awaited: false,
            },
          }),
        ),
      );
      setToast({ title: "Stopping Scout", type: ToastType.SUCCESS });
    } catch (error) {
      console.error("Failed to stop Scout:", error);
      setToast({ title: "Failed to stop Scout", type: ToastType.ERROR });
    } finally {
      setIsRequestInFlight(false);
    }
  };

  const latestUserMessageId = userMessageIds[userMessageIds.length - 1];
  const imbueScoutCheck = latestUserMessageId && checksData?.[latestUserMessageId]?.["Scout"];
  const hasImbueScoutRun = imbueScoutCheck && imbueScoutCheck.runIds && imbueScoutCheck.runIds.length > 0;

  let isImbueScoutRunning = false;
  const runningChecks: Array<{ userMessageId: string; runId: string }> = [];
  if (imbueScoutCheck && latestUserMessageId) {
    const latestRunId = imbueScoutCheck.runIds?.[imbueScoutCheck.runIds.length - 1];
    const latestStatus = latestRunId ? imbueScoutCheck.statusByRunId[latestRunId] : null;
    const checkStatus = getCheckStatusDisplay(latestStatus);
    if (checkStatus === CheckStatusDisplay.RUNNING) {
      isImbueScoutRunning = true;
      runningChecks.push({ userMessageId: latestUserMessageId, runId: latestRunId });
    }
  }

  const lastAssistantMessageIndex = chatMessages
    ? chatMessages.reduceRight((acc, msg, idx) => {
        return acc === -1 && msg.role === ChatMessageRole.ASSISTANT ? idx : acc;
      }, -1)
    : -1;
  const isLatestAssistantMessage = lastAssistantMessageIndex === (chatMessages?.length ?? 0) - 1;

  const isButtonClickable = isLatestAssistantMessage && !isRequestInFlight && task?.status !== "RUNNING";

  if (task?.status === "BUILDING") {
    return null;
  }

  // Render status display in the middle section
  const renderStatusDisplay = (): ReactElement | null => {
    if (isImbueScoutRunning) {
      return (
        <Flex align="center" gap="2">
          <Spinner size="1" />
          <Text size="2" className={styles.statusText}>
            Running...
          </Text>
        </Flex>
      );
    }

    if (scoutStatusData.status === "complete" && scoutStatusData.score !== undefined) {
      const scoreColor = getScoreColor(scoutStatusData.score);
      const scorePercent = Math.round(scoutStatusData.score * 100);
      return (
        <Flex align="center" gap="2">
          <Text size="2" className={styles.statusText}>
            Completed
          </Text>
          <Badge
            className={styles.scoreBadge}
            style={{
              backgroundColor: scoreColor,
            }}
          >
            {scorePercent}%
          </Badge>
        </Flex>
      );
    }
    return null;
  };

  return (
    <>
      <Box className={styles.wrapper}>
        <Box className={styles.container} style={{ position: "relative" }}>
          <Flex align="center" gap="3" className={styles.header} style={{ padding: "8px 18px" }}>
            {/* Left: Title */}
            <Text size="2" className={styles.titleText}>
              Scout
            </Text>

            {/* Middle: Status Display */}
            {renderStatusDisplay()}
            {/* Spacer */}
            <Box style={{ flex: 1 }} />

            {/* Right: Action Button/Dropdown */}
            {isImbueScoutRunning ? (
              <Tooltip content="Stop Scout">
                <IconButton size="1" variant="ghost" className={styles.actionButton} onClick={handleStopScout}>
                  <Spinner size="1" />
                </IconButton>
              </Tooltip>
            ) : (
              <DropdownMenu.Root>
                <DropdownMenu.Trigger>
                  <IconButton
                    size="1"
                    variant="ghost"
                    className={isButtonClickable ? styles.actionButton : styles.actionButtonDisabled}
                    disabled={!isButtonClickable}
                  >
                    {hasImbueScoutRun ? <Repeat size={14} /> : <Play size={14} />}
                    <ChevronDownIcon className={styles.chevronIcon} />
                  </IconButton>
                </DropdownMenu.Trigger>
                <DropdownMenu.Content size="1">
                  <DropdownMenu.Item onClick={handleRunScout}>
                    {hasImbueScoutRun ? <Repeat size={12} /> : <Play size={12} />}
                    {hasImbueScoutRun ? "Re-run Scout" : "Run Scout"}
                  </DropdownMenu.Item>
                  <DropdownMenu.Item onClick={() => setIsAutoRunEnabled(!isAutoRunEnabled)}>
                    <Flex align="center" gap="2" style={{ width: "100%" }}>
                      <Play size={12} />
                      <Text>Auto-run Scout</Text>
                      <Box style={{ flex: 1 }} />
                      {isAutoRunEnabled && <CheckIcon />}
                    </Flex>
                  </DropdownMenu.Item>
                </DropdownMenu.Content>
              </DropdownMenu.Root>
            )}
          </Flex>
        </Box>
      </Box>
      <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} title={toast?.title} type={toast?.type} />
    </>
  );
};
