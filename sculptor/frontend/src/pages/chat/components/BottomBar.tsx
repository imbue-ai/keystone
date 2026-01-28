import { Box, Button, Flex, Popover, Skeleton, Text, Tooltip } from "@radix-ui/themes";
import { useAtomValue } from "jotai";
import { TriangleAlert } from "lucide-react";
import type { ReactElement } from "react";
import { useMemo, useState } from "react";

import { healthCheckDataAtom } from "~/common/state/atoms/backend.ts";
import { VersionDisplay } from "~/components/VersionDisplay.tsx";
import { isMac } from "~/electron/utils.ts";

import { compactTask, ElementIds, LlmModel } from "../../../api";
import {
  MAX_CONTEXT_WINDOW_TOKENS_BEFORE_FORCED_COMPACT_BY_MODEL,
  TOTAL_CONTEXT_WINDOW_TOKENS_BY_MODEL,
} from "../../../common/Constants.ts";
import { useTaskPageParams } from "../../../common/NavigateUtils.ts";
import { useTask } from "../../../common/state/hooks/useTaskHelpers.ts";
import { Toast, type ToastContent, ToastType } from "../../../components/Toast.tsx";
import styles from "./BottomBar.module.scss";
import { CompactAutomaticAlert } from "./CompactAutomaticAlert";

type BottomBarProps = {
  tokensUsed: number | undefined;
};

export const ContextStatus = {
  ALERT: "alert",
  WARNING: "warning",
  STANDARD: "standard",
} as const;

type ContextStatus = (typeof ContextStatus)[keyof typeof ContextStatus];

export const BottomBar = ({ tokensUsed }: BottomBarProps): ReactElement => {
  const [isCompactingInitially, setIsCompactingInitially] = useState(false);
  const [isPopoverOpen, setIsPopoverOpen] = useState(false);
  const [isAlertWindowOpen, setIsAlertWindowOpen] = useState(false);
  const [hasShownAlertWindow, setHasShownAlertWindow] = useState(false);
  const { taskID, projectID } = useTaskPageParams();
  const [toast, setToast] = useState<ToastContent | null>(null);
  const healthCheckData = useAtomValue(healthCheckDataAtom);

  const task = useTask(taskID);
  const isCompacting = task?.isCompacting;

  const model = task?.model ?? LlmModel.CLAUDE_4_SONNET;
  const totalContextTokens = TOTAL_CONTEXT_WINDOW_TOKENS_BY_MODEL[model];
  const maxContextTokensBeforeCompact = MAX_CONTEXT_WINDOW_TOKENS_BEFORE_FORCED_COMPACT_BY_MODEL[model];
  const contextPercentage = Math.round(((tokensUsed || 0) / totalContextTokens) * 100);

  const percentageRemaining = 100 - contextPercentage;

  const variant: ContextStatus = useMemo(() => {
    if (tokensUsed === undefined) {
      return ContextStatus.STANDARD;
    }

    if (tokensUsed >= maxContextTokensBeforeCompact) {
      if (!hasShownAlertWindow) {
        setIsAlertWindowOpen(true);
        setHasShownAlertWindow(true);
      }
    }

    if (percentageRemaining < 10) {
      return ContextStatus.ALERT;
    }
    if (percentageRemaining >= 10 && percentageRemaining <= 50) return ContextStatus.WARNING;

    return ContextStatus.STANDARD;
  }, [tokensUsed, hasShownAlertWindow, percentageRemaining, maxContextTokensBeforeCompact]);

  const handleCompact = async (): Promise<void> => {
    if (isCompacting || isCompactingInitially) {
      return;
    }

    setIsCompactingInitially(true);
    setIsPopoverOpen(false);
    try {
      await compactTask({
        path: { project_id: projectID, task_id: taskID },
        // Compaction can take a while, so we'll set a 2 minute timeout for the API call
        meta: { wsTimeout: 120000 },
      });
    } catch (error) {
      console.error("Error compacting context:", error);
      setToast({ title: "Failed to compact context", type: ToastType.ERROR });
    }
    setIsCompactingInitially(false);
    setIsAlertWindowOpen(false);
    setHasShownAlertWindow(false);
  };

  const renderContent = (): ReactElement => {
    if (variant === ContextStatus.ALERT) {
      return (
        <Flex className={styles.alertContent} align="center" gap="2">
          <TriangleAlert size={16} color="var(--red-11)" />
          <Text className={`${styles.statusText} ${styles.alertText}`}>
            &lt;10% Context remaining, compact now to prevent losing work
          </Text>
        </Flex>
      );
    }

    if (tokensUsed === undefined) {
      return (
        <Skeleton>
          {" "}
          <Text className={styles.statusText}>{100}% Context Remaining</Text>{" "}
        </Skeleton>
      );
    }
    return <Text className={styles.statusText}>{percentageRemaining}% Context Remaining</Text>;
  };

  const ramUsed = healthCheckData?.sculptorContainerRamUsedGb
    ? Math.round(healthCheckData!.sculptorContainerRamUsedGb * 100) / 100
    : 0;
  const ramLimit = healthCheckData?.dockerRamLimitGb
    ? Math.round(healthCheckData!.dockerRamLimitGb! * 100) / 100
    : undefined;

  const ramTooltipText = isMac()
    ? "Sculptor performance may degrade when the RAM usage gets close to Docker's limit. The total RAM limit for the Docker VM is configurable in Docker Desktop -> Settings -> Resources."
    : "Sculptor performance may degrade when the RAM usage gets close to your machine's total RAM.";
  return (
    <>
      <Flex className={styles.bottomBar} height="32px" align="center" justify="between" pl="9px">
        <Popover.Root open={isPopoverOpen} onOpenChange={setIsPopoverOpen}>
          <Popover.Trigger>
            <Flex align="center" gapX="2" className={styles.actionButton}>
              {tokensUsed === undefined ? (
                <Skeleton>
                  <Box
                    className={`${styles.progressBar} ${styles[variant]}`}
                    position="relative"
                    width="98px"
                    height="10px"
                    style={{ borderRadius: "10px", overflow: "hidden", flexShrink: 0 }}
                  >
                    <Box
                      className={`${styles.progressFill} ${styles[variant]}`}
                      position="absolute"
                      height="10px"
                      top="0"
                      left="0"
                      style={{ width: `${contextPercentage}%`, borderRadius: "10px" }}
                    />
                  </Box>
                </Skeleton>
              ) : (
                <Flex data-testid={ElementIds.COMPACTION_BAR} align="center" gapX="2">
                  <Box
                    className={`${styles.progressBar} ${styles[variant]}`}
                    position="relative"
                    width="98px"
                    height="10px"
                    style={{ borderRadius: "10px", overflow: "hidden", flexShrink: 0 }}
                  >
                    <Box
                      className={`${styles.progressFill} ${styles[variant]}`}
                      position="absolute"
                      height="10px"
                      top="0"
                      left="0"
                      style={{ width: `${contextPercentage}%`, borderRadius: "10px" }}
                    />
                  </Box>
                  {isCompacting || isCompactingInitially ? (
                    <Text className={styles.statusText}>Compacting...</Text>
                  ) : (
                    renderContent()
                  )}
                </Flex>
              )}
            </Flex>
          </Popover.Trigger>
          <Popover.Content
            className={styles.popoverContent}
            width="177px"
            height="136px"
            align="start"
            data-testid="COMPACTION_PANEL"
          >
            {isCompacting || isCompactingInitially || task?.status !== "READY" ? (
              <Tooltip content="Please wait for the assistant to finish">
                <Button
                  onClick={handleCompact}
                  disabled={true}
                  className={styles.popoverButton}
                  variant="solid"
                  data-testid="COMPACTION_BUTTON"
                >
                  Compact Context
                </Button>
              </Tooltip>
            ) : (
              <Button onClick={handleCompact} variant="solid" data-testid="COMPACTION_BUTTON">
                Compact Context
              </Button>
            )}
            <Text className={styles.popoverDescription}>
              Compact the context from this session into a summary. You cannot undo this action.
            </Text>
          </Popover.Content>
        </Popover.Root>
        <CompactAutomaticAlert
          open={isAlertWindowOpen && !!tokensUsed && tokensUsed >= maxContextTokensBeforeCompact}
          onOpenChange={setIsAlertWindowOpen}
          onCompactClick={handleCompact}
          tokenInfoText={`${contextPercentage}% context used`}
          disabled={isCompacting || isCompactingInitially}
        />
        <Flex align="baseline" gapX="3" mr="5">
          <Tooltip content={ramTooltipText}>
            <Text color="gold" className={styles.ramUsageText}>
              <Text className={styles.bolder}>{ramLimit ? `${ramUsed} / ${ramLimit}GB` : `${ramUsed}GB`}</Text>
              <Text>{" RAM usage"}</Text>
            </Text>
          </Tooltip>
          <VersionDisplay />
        </Flex>
      </Flex>
      <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} title={toast?.title} type={toast?.type} />
    </>
  );
};
