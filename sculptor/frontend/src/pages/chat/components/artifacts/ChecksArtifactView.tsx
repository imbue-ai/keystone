import { CheckIcon, PlayIcon, StopIcon } from "@radix-ui/react-icons";
import { Button, Flex, Spinner, Text, Tooltip } from "@radix-ui/themes";
import { useAtomValue } from "jotai";
import { type ReactElement, useMemo, useState } from "react";

import { userConfigAtom } from "~/common/state/atoms/userConfig.ts";

import { useTaskPageParams } from "../../../../common/NavigateUtils.ts";
import { Toast, type ToastContent, ToastType } from "../../../../components/Toast.tsx";
import type { ArtifactViewContentProps, ArtifactViewTabLabelProps, Check, CheckHistory } from "../../Types.ts";
import {
  CheckStatusDisplay,
  type CheckStatusDisplay as CheckStatusDisplayType,
  getCheckStatusDisplay,
} from "../../utils/checkStatusUtils";
import { filterChecksWithCommands, filterEnabledChecks, restartCheck, stopCheck } from "../../utils/checkUtils";
import styles from "./ChecksArtifactView.module.scss";

type CheckItemProps = {
  check: Check;
  index: number;
  checkHistory?: CheckHistory;
  messageId: string;
  areChecksReady?: boolean;
};

const CheckItem = ({
  check,
  index: _index,
  checkHistory,
  messageId,
  areChecksReady = true,
}: CheckItemProps): ReactElement => {
  const [toast, setToast] = useState<ToastContent | null>(null);
  const [isRequestInFlight, setIsRequestInFlight] = useState(false);
  const { projectID, taskID } = useTaskPageParams();

  const latestRunId = checkHistory?.runIds?.[checkHistory.runIds.length - 1];
  const latestStatus = latestRunId ? checkHistory.statusByRunId[latestRunId] : null;
  const displayStatus = getCheckStatusDisplay(latestStatus);

  const getStatusIcon = (status: CheckStatusDisplayType): ReactElement => {
    const getIconContent = (): ReactElement => {
      switch (status) {
        case CheckStatusDisplay.IDLE:
          return <div className={styles.idleCircle}></div>;
        case CheckStatusDisplay.RUNNING:
          return <Spinner size="1" />;
        case CheckStatusDisplay.PASSED:
          return (
            <div className={styles.checkCircle}>
              <CheckIcon className={styles.checkMark} />
            </div>
          );
        case CheckStatusDisplay.FAILED:
          return (
            <div className={styles.failedCircle}>
              <span className={styles.failedMark}>✕</span>
            </div>
          );
        case CheckStatusDisplay.PAUSED:
          return <div className={styles.idleCircle}></div>;
        default:
          return <div className={styles.idleCircle}></div>;
      }
    };

    return (
      <Tooltip content={check.name}>
        <div className={styles.statusIcon}>{getIconContent()}</div>
      </Tooltip>
    );
  };

  const getActionButton = (status: CheckStatusDisplayType): ReactElement => {
    const handleRestartCheck = async (): Promise<void> => {
      setIsRequestInFlight(true);
      try {
        await restartCheck(projectID, taskID, check.name, messageId);
        setToast({ title: `Re-running ${check.name}`, type: ToastType.SUCCESS });
      } catch (error) {
        console.error("Failed to restart check:", error);
        setToast({ title: `Failed to restart ${check.name}`, type: ToastType.ERROR });
      } finally {
        setIsRequestInFlight(false);
      }
    };

    const handleStopCheck = async (): Promise<void> => {
      setIsRequestInFlight(true);
      try {
        if (latestRunId) {
          await stopCheck(projectID, taskID, check.name, latestRunId, messageId);
          setToast({ title: `Stopping ${check.name}`, type: ToastType.SUCCESS });
        }
      } catch (error) {
        console.error("Failed to stop check:", error);
        setToast({ title: `Failed to stop ${check.name}`, type: ToastType.ERROR });
      } finally {
        setIsRequestInFlight(false);
      }
    };

    const isRunning = status === CheckStatusDisplay.RUNNING;
    const icon = isRunning ? <StopIcon /> : <PlayIcon />;
    const onClick = isRunning ? handleStopCheck : handleRestartCheck;

    return (
      <Button
        size="1"
        variant="ghost"
        onClick={onClick}
        className={styles.actionButton}
        disabled={isRequestInFlight || !check.isEnabled || !areChecksReady}
      >
        {icon}
      </Button>
    );
  };

  return (
    <div className={styles.checkItem}>
      <Flex align="center" gap="3" py="1" px="2">
        {getStatusIcon(displayStatus)}
        <Flex flexGrow="1" minWidth="0">
          <Text className={styles.checkTitle}>{check.name}</Text>
        </Flex>
        <div onClick={(e) => e.stopPropagation()}>{getActionButton(displayStatus)}</div>
      </Flex>
      <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} title={toast?.title} type={toast?.type} />
    </div>
  );
};

export const ChecksViewComponent = ({
  checksData,
  userMessageIds,
  task: _task,
  checksDefinedForMessage,
}: ArtifactViewContentProps): ReactElement => {
  // Use the provided user message IDs
  const availableMessageIds = userMessageIds || [];
  const currentMessageId = availableMessageIds[availableMessageIds.length - 1] || "current";
  const userConfig = useAtomValue(userConfigAtom);

  // Memoize allCheckNames to prevent unnecessary recalculations
  const allCheckNames = useMemo(() => {
    if (!checksData) return new Set<string>();

    const checkNames = new Set<string>();

    Object.values(checksData).forEach((checkHistoryByName) => {
      Object.keys(checkHistoryByName).forEach((checkName) => {
        checkNames.add(checkName);
      });
    });

    return checkNames;
  }, [checksData]);

  // Memoize checkHistoryByName to prevent unnecessary recalculations
  const checkHistoryByName = useMemo(() => {
    if (!checksData) return {};

    const currentHistory = checksData[currentMessageId] || {};

    // If current message has no checks, find the most recent check status for each check
    if (Object.keys(currentHistory).length === 0) {
      const allMessageIds = Object.keys(checksData);
      const fallbackHistory: Record<string, CheckHistory> = {};

      allCheckNames.forEach((checkName) => {
        let mostRecentHistory: CheckHistory | null = null;
        let mostRecentRunId: string | null = null;

        // Look through all messages to find the most recent run for this check
        allMessageIds.forEach((messageId) => {
          const messageCheckHistory = checksData[messageId];
          if (messageCheckHistory && messageCheckHistory[checkName]) {
            const history = messageCheckHistory[checkName];
            const latestRunId = history.runIds?.[history.runIds.length - 1];
            if (latestRunId && (!mostRecentRunId || latestRunId > mostRecentRunId)) {
              mostRecentHistory = history;
              mostRecentRunId = latestRunId;
            }
          }
        });

        if (mostRecentHistory) {
          fallbackHistory[checkName] = mostRecentHistory;
        }
      });

      return fallbackHistory;
    }

    return currentHistory;
  }, [checksData, currentMessageId, allCheckNames]);

  if (!checksData) {
    return (
      <Flex className={styles.noChecks} justify="center" align="center" p="3">
        <Text color="gray">No checks defined</Text>
      </Flex>
    );
  }

  let enabledCheckNames: Array<string> = Array.from(allCheckNames);
  if (userConfig !== null) {
    enabledCheckNames = filterEnabledChecks(enabledCheckNames, userConfig);
  }

  if (enabledCheckNames.length === 0) {
    return (
      <Flex className={styles.noChecks} justify="center" align="center" p="3">
        <Text color="gray">No checks defined</Text>
      </Flex>
    );
  }

  const checksWithCommands = filterChecksWithCommands(enabledCheckNames, checkHistoryByName);

  const areChecksReady = checksDefinedForMessage?.has(currentMessageId) ?? false;

  if (checksWithCommands.length === 0) {
    return (
      <Flex className={styles.noChecks} justify="center" align="center" p="3">
        <Text color="gray">No checks defined</Text>
      </Flex>
    );
  }

  return (
    <Flex direction="column" gap="0" className={styles.checksContainer}>
      {checksWithCommands.map((checkName: string, index: number) => {
        const checkHistory = checkHistoryByName[checkName];
        const latestRunId = checkHistory?.runIds?.[checkHistory.runIds.length - 1];
        const latestStatus = latestRunId ? checkHistory.statusByRunId[latestRunId] : null;
        const fullCheck = latestStatus?.check || { name: checkName, command: null };

        return (
          <CheckItem
            key={index}
            check={fullCheck}
            index={index}
            checkHistory={checkHistory}
            messageId={currentMessageId}
            areChecksReady={areChecksReady}
          />
        );
      })}
    </Flex>
  );
};

export const ChecksTabLabelComponent = ({ artifacts: _artifacts }: ArtifactViewTabLabelProps): ReactElement => {
  return (
    <Flex align="center" gap="2">
      <div className={styles.tabCheckIcon}>
        <div className={styles.tabCheckCircle}>
          <CheckIcon className={styles.tabCheckMark} />
        </div>
      </div>
      Checks
    </Flex>
  );
};
