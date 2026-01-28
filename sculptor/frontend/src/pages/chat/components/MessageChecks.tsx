import { CheckIcon } from "@radix-ui/react-icons";
import { Flex, Spinner, Tooltip } from "@radix-ui/themes";
import { useAtomValue } from "jotai";
import { type ReactElement, useState } from "react";

import { useImbueParams } from "../../../common/NavigateUtils.ts";
import { areSuggestionsEnabledAtom, userConfigAtom } from "../../../common/state/atoms/userConfig.ts";
import { Toast, type ToastContent, ToastType } from "../../../components/Toast.tsx";
import type { CheckHistory } from "../Types.ts";
import {
  CheckStatusDisplay,
  type CheckStatusDisplay as CheckStatusDisplayType,
  getCheckStatusDisplay,
} from "../utils/checkStatusUtils";
import { filterChecksWithCommands, filterEnabledChecks, restartCheck, stopCheck } from "../utils/checkUtils";
import styles from "./MessageChecks.module.scss";

type MessageChecksProps = {
  messageId: string;
  messageRole: string;
  checksData?: Record<string, Record<string, CheckHistory>>;
  isLastAssistantMessage?: boolean;
  onShowChecks?: () => void;
  userMessageId?: string;
  isLastMessageOverall?: boolean;
};

export const MessageChecks = ({
  messageId: _messageId,
  messageRole,
  checksData,
  isLastAssistantMessage = false,
  onShowChecks,
  userMessageId,
  isLastMessageOverall = false,
}: MessageChecksProps): ReactElement => {
  const areSuggestionsEnabled = useAtomValue(areSuggestionsEnabledAtom);
  const userConfig = useAtomValue(userConfigAtom);

  if (!areSuggestionsEnabled) {
    return <></>;
  }

  if (messageRole !== "ASSISTANT") {
    return <></>;
  }

  if (!checksData) {
    return <></>;
  }

  // Check if checksData is valid
  if (typeof checksData !== "object") {
    console.warn("MessageChecks: checksData is invalid");
    return <></>;
  }

  if (!userMessageId) {
    return <></>;
  }

  const checkHistoryByName = checksData[userMessageId] || {};

  if (Object.keys(checkHistoryByName).length === 0) {
    return <></>;
  }

  let enabledCheckNames: Array<string> = Object.keys(checkHistoryByName);
  if (userConfig !== null) {
    enabledCheckNames = filterEnabledChecks(enabledCheckNames, userConfig);
  }

  const checksWithCommands = filterChecksWithCommands(enabledCheckNames, checkHistoryByName);

  return (
    <Flex direction="row" gap="1" className={styles.checksContainer} justify="end">
      {checksWithCommands.map((checkName, index) => {
        const checkHistory = checkHistoryByName[checkName];
        return (
          <CheckItem
            key={index}
            checkName={checkName}
            checkHistory={checkHistory}
            onShowChecks={onShowChecks}
            userMessageId={userMessageId}
            isLatestCheckMessage={isLastAssistantMessage && isLastMessageOverall}
          />
        );
      })}
    </Flex>
  );
};

type CheckItemProps = {
  checkName: string;
  checkHistory?: CheckHistory;
  onShowChecks?: () => void;
  userMessageId?: string;
  isLatestCheckMessage?: boolean;
};

const CheckItem = ({
  checkName,
  checkHistory,
  onShowChecks: _onShowChecks,
  userMessageId,
  isLatestCheckMessage = false,
}: CheckItemProps): ReactElement => {
  const [toast, setToast] = useState<ToastContent | null>(null);
  const [isRequestInFlight, setIsRequestInFlight] = useState(false);
  const { projectID, taskID } = useImbueParams();

  if (!projectID) {
    throw new Error("Expected projectID to be defined");
  }

  if (!taskID) {
    throw new Error("Expected taskID to be defined");
  }

  const latestRunId = checkHistory?.runIds?.[checkHistory.runIds.length - 1];
  const latestStatus = latestRunId ? checkHistory.statusByRunId[latestRunId] : null;
  const displayStatus = getCheckStatusDisplay(latestStatus);

  const getTooltipContent = (status: CheckStatusDisplayType, isClickable: boolean): string => {
    if (!isClickable) {
      return checkName;
    }

    switch (status) {
      case CheckStatusDisplay.RUNNING:
        return `Stop ${checkName}`;
      case CheckStatusDisplay.IDLE:
      case CheckStatusDisplay.PAUSED:
        return `Run ${checkName}`;
      case CheckStatusDisplay.PASSED:
      case CheckStatusDisplay.FAILED:
        return `Re-run ${checkName}`;
      default:
        return checkName;
    }
  };

  const handleRestartCheck = async (): Promise<void> => {
    if (!userMessageId) return;
    setIsRequestInFlight(true);
    try {
      await restartCheck(projectID, taskID, checkName, userMessageId);
      setToast({ title: `Re-running ${checkName}`, type: ToastType.SUCCESS });
    } catch (error) {
      console.error("Failed to restart check:", error);
      setToast({ title: `Failed to restart ${checkName}`, type: ToastType.ERROR });
    } finally {
      setIsRequestInFlight(false);
    }
  };

  const handleStopCheck = async (): Promise<void> => {
    if (!userMessageId) return;
    setIsRequestInFlight(true);
    try {
      if (latestRunId && checkHistory?.runIds?.length > 0) {
        const runIds = checkHistory.runIds;
        const lastRunId = runIds[runIds.length - 1];
        if (lastRunId) {
          await stopCheck(projectID, taskID, checkName, lastRunId, userMessageId);
          setToast({ title: `Stopping ${checkName}`, type: ToastType.SUCCESS });
        }
      }
    } catch (error) {
      console.error("Failed to stop check:", error);
      setToast({ title: `Failed to stop ${checkName}`, type: ToastType.ERROR });
    } finally {
      setIsRequestInFlight(false);
    }
  };

  const getStatusIcon = (status: CheckStatusDisplayType): ReactElement => {
    const getIconContent = (): ReactElement => {
      switch (status) {
        case CheckStatusDisplay.IDLE:
          return <div className={styles.idleIcon}></div>;
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
          return <div className={styles.idleIcon}></div>;
        default:
          return <div className={styles.idleIcon}></div>;
      }
    };

    const isRunning = status === CheckStatusDisplay.RUNNING;
    const className = styles.clickableStatusIcon;

    const isClickable = (isRunning || isLatestCheckMessage) && !isRequestInFlight;
    const onClick = isClickable ? (isRunning ? handleStopCheck : handleRestartCheck) : undefined;
    const cursor = isClickable ? "pointer" : "default";

    return (
      <Tooltip content={getTooltipContent(status, isClickable)}>
        <div className={className} onClick={onClick} style={{ cursor }}>
          {getIconContent()}
        </div>
      </Tooltip>
    );
  };

  return (
    <>
      <div className={styles.checkItem}>{getStatusIcon(displayStatus)}</div>
      <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} title={toast?.title} type={toast?.type} />
    </>
  );
};
