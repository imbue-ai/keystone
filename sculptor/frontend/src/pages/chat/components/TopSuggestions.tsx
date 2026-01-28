import { ChevronDownIcon } from "@radix-ui/react-icons";
import { Badge, Box, Flex, IconButton, ScrollArea, Spinner, Text, Tooltip } from "@radix-ui/themes";
import { useSetAtom } from "jotai";
import { Play, Repeat } from "lucide-react";
import type { ReactElement } from "react";
import { useEffect, useRef, useState } from "react";

import type { ChatMessage } from "../../../api";
import { ChatMessageRole, sendMessageGeneric } from "../../../api";
import { useTaskPageParams } from "../../../common/NavigateUtils.ts";
import { navigateToMostRecentSuggestionsTurnAtomFamily } from "../../../common/state/atoms/tasks.ts";
import { useTask } from "../../../common/state/hooks/useTaskHelpers";
import { Toast, type ToastContent, ToastType } from "../../../components/Toast.tsx";
import type { CheckHistory, SuggestionsData } from "../Types.ts";
import { CheckStatusDisplay, getCheckStatusDisplay } from "../utils/checkStatusUtils";
import { SuggestionItem } from "./artifacts/SuggestionItem.tsx";
import { useMostRecentSuggestions, useUserMessageIds } from "./artifacts/useSuggestions.ts";
import styles from "./TopSuggestions.module.scss";

type TopSuggestionsProps = {
  suggestionsData: SuggestionsData | undefined;
  onShowSuggestions?: () => void;
  chatMessages?: Array<ChatMessage>;
  appendTextRef?: React.MutableRefObject<((text: string) => void) | null>;
  checksData?: Record<string, Record<string, CheckHistory>>;
  onFlashTab?: (tabId: string) => void;
};

export const TopSuggestions = ({
  suggestionsData,
  onShowSuggestions,
  chatMessages,
  appendTextRef,
  checksData,
  onFlashTab,
}: TopSuggestionsProps): ReactElement | null => {
  const { projectID, taskID } = useTaskPageParams();
  const setNavigateToMostRecentTurn = useSetAtom(navigateToMostRecentSuggestionsTurnAtomFamily(taskID ?? ""));
  const userMessageIds = useUserMessageIds(chatMessages);
  const allSuggestions = useMostRecentSuggestions(suggestionsData, chatMessages);
  const [isExpanded, setIsExpanded] = useState(false);
  const [hasBeenHovered, setHasBeenHovered] = useState(false);
  const [toast, setToast] = useState<ToastContent | null>(null);
  const [isRequestInFlight, setIsRequestInFlight] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  const task = useTask(taskID ?? "");

  const handleRerunVerify = async (e: React.MouseEvent): Promise<void> => {
    e.stopPropagation();
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
            check_name: "Verifier",
            user_message_id: latestUserMessageId,
          },
          is_awaited: false,
        },
      });
    } catch (error) {
      console.error("Failed to rerun Verifier:", error);
    } finally {
      setIsRequestInFlight(false);
    }
  };

  const handleStopVerify = async (e: React.MouseEvent): Promise<void> => {
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
                check_name: "Verifier",
                run_id: check.runId,
                user_message_id: check.userMessageId,
              },
              is_awaited: false,
            },
          }),
        ),
      );
      setToast({ title: "Stopping Verifier", type: ToastType.SUCCESS });
    } catch (error) {
      console.error("Failed to stop Verifier:", error);
      setToast({ title: "Failed to stop Verifier", type: ToastType.ERROR });
    } finally {
      setIsRequestInFlight(false);
    }
  };

  const hasSuggestions = allSuggestions.length > 0;
  const MAX_VISIBLE_SUGGESTIONS = 3;
  const visibleSuggestions = allSuggestions.slice(0, MAX_VISIBLE_SUGGESTIONS);
  const itemCount = visibleSuggestions.length;
  const expandedHeight = itemCount * 28 + 4;

  useEffect(() => {
    setIsExpanded(false);
  }, [taskID]);

  useEffect(() => {
    if (allSuggestions.length === 0) {
      setIsExpanded(false);
    } else if (allSuggestions.length > 0) {
      setIsExpanded(true);
    }
  }, [allSuggestions.length]);

  const latestUserMessageId = userMessageIds[userMessageIds.length - 1];

  const imbueVerifyCheck = latestUserMessageId && checksData?.[latestUserMessageId]?.["Verifier"];
  const hasImbueVerifyRun = imbueVerifyCheck && imbueVerifyCheck.runIds && imbueVerifyCheck.runIds.length > 0;

  let isImbueVerifyRunning = false;
  const runningChecks: Array<{ userMessageId: string; runId: string }> = [];

  if (checksData && latestUserMessageId) {
    const imbueVerifyCheck = checksData[latestUserMessageId]?.["Verifier"];
    if (imbueVerifyCheck) {
      const latestRunId = imbueVerifyCheck.runIds?.[imbueVerifyCheck.runIds.length - 1];
      const latestStatus = latestRunId ? imbueVerifyCheck.statusByRunId[latestRunId] : null;
      const checkStatus = getCheckStatusDisplay(latestStatus);
      if (checkStatus === CheckStatusDisplay.RUNNING) {
        isImbueVerifyRunning = true;
        runningChecks.push({ userMessageId: latestUserMessageId, runId: latestRunId });
      }
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

  return (
    <>
      <Box className={styles.wrapper}>
        <Box
          className={styles.container}
          ref={containerRef}
          onMouseLeave={() => setHasBeenHovered(false)}
          style={{ position: "relative" }}
        >
          <Flex direction="column" gap="0">
            <Flex align="center" gap="2" className={styles.header} style={{ justifyContent: "space-between" }}>
              <Flex
                align="center"
                gap="2"
                onClick={hasSuggestions ? (): void => setIsExpanded(!isExpanded) : undefined}
                style={{ cursor: hasSuggestions ? "pointer" : "not-allowed", marginLeft: "18px" }}
              >
                <ChevronDownIcon
                  className={styles.chevron}
                  style={{
                    transform: isExpanded ? "rotate(0deg)" : "rotate(-90deg)",
                    opacity: hasSuggestions ? 1 : 0.3,
                  }}
                />
                <Text
                  size="2"
                  style={{
                    color: "var(--gold-12)",
                    fontSize: "14px",
                    fontWeight: "500",
                    lineHeight: "1",
                    display: "flex",
                    alignItems: "center",
                  }}
                >
                  Suggestions
                </Text>
                <Badge style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: "500", fontSize: "12px" }}>
                  {allSuggestions.length}
                </Badge>
              </Flex>
              <Flex align="center" gap="2" style={{ marginRight: "18px" }}>
                {isImbueVerifyRunning && (
                  <>
                    <Text
                      size="1"
                      style={{
                        color: "var(--gold-9)",
                        fontSize: "12px",
                        lineHeight: "1",
                        display: "flex",
                        alignItems: "center",
                      }}
                    >
                      Running Verifier...
                    </Text>
                    <Tooltip content="Stop Verifier">
                      <Box
                        onClick={handleStopVerify}
                        style={{
                          cursor: "pointer",
                          display: "flex",
                          alignItems: "center",
                        }}
                      >
                        <Spinner size="1" />
                      </Box>
                    </Tooltip>
                  </>
                )}
                {hasSuggestions &&
                  isExpanded &&
                  allSuggestions.length > MAX_VISIBLE_SUGGESTIONS &&
                  !isImbueVerifyRunning && (
                    <Text
                      size="1"
                      style={{
                        color: "var(--gold-10)",
                        fontSize: "12px",
                        cursor: "pointer",
                        textDecoration: "underline",
                      }}
                      onClick={(e) => {
                        e.stopPropagation();
                        setNavigateToMostRecentTurn(Date.now());
                        onShowSuggestions?.();
                        onFlashTab?.("Suggestions");
                      }}
                    >
                      +{allSuggestions.length - MAX_VISIBLE_SUGGESTIONS} more
                    </Text>
                  )}
                {!isImbueVerifyRunning && (
                  <Tooltip
                    content={
                      isButtonClickable
                        ? hasImbueVerifyRun
                          ? "Re-run Verifier"
                          : "Run Verifier"
                        : "Available after agent turn"
                    }
                  >
                    <IconButton
                      size="1"
                      variant="ghost"
                      style={{
                        color: isButtonClickable ? "var(--gold-9)" : "var(--gray-6)",
                        cursor: isButtonClickable ? "pointer" : "not-allowed",
                      }}
                      onClick={isButtonClickable ? handleRerunVerify : undefined}
                      disabled={!isButtonClickable}
                    >
                      {hasImbueVerifyRun ? <Repeat size={14} /> : <Play size={14} />}
                    </IconButton>
                  </Tooltip>
                )}
              </Flex>
            </Flex>
            {allSuggestions.length > 0 && (
              <ScrollArea
                ref={scrollContainerRef}
                className={`${styles.scrollContainer} ${isExpanded ? styles.scrollContainerExpanded : ""}`}
                style={isExpanded ? { height: `${expandedHeight}px`, maxHeight: `${expandedHeight}px` } : undefined}
              >
                <Flex direction="column" gap="0" className={styles.itemsList}>
                  {visibleSuggestions.map((suggestion, index) => (
                    <Box key={index} className={styles.itemWrapper}>
                      <SuggestionItem
                        suggestion={suggestion}
                        appendTextRef={appendTextRef}
                        hasBeenHovered={hasBeenHovered}
                        onFirstHover={() => setHasBeenHovered(true)}
                        projectID={projectID}
                        taskID={taskID}
                      />
                    </Box>
                  ))}
                </Flex>
              </ScrollArea>
            )}
          </Flex>
        </Box>
      </Box>
      <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} title={toast?.title} type={toast?.type} />
    </>
  );
};
