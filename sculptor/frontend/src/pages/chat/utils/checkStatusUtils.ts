import { CheckFinishedReason } from "../../../api";
import type { CheckStatus } from "../Types.ts";

export const CheckStatusDisplay = {
  IDLE: "idle",
  RUNNING: "running",
  PASSED: "passed",
  FAILED: "failed",
  PAUSED: "paused",
} as const;

export type CheckStatusDisplay = (typeof CheckStatusDisplay)[keyof typeof CheckStatusDisplay];

export const getCheckStatusDisplay = (checkStatus: CheckStatus | null): CheckStatusDisplay => {
  if (!checkStatus) return CheckStatusDisplay.IDLE;

  if (checkStatus.startedAt && !checkStatus.finishedReason) {
    return CheckStatusDisplay.RUNNING;
  }

  if (checkStatus.finishedReason) {
    if (checkStatus.finishedReason === CheckFinishedReason.FINISHED) {
      return checkStatus.exitCode === 0 || checkStatus.exitCode === null
        ? CheckStatusDisplay.PASSED
        : CheckStatusDisplay.FAILED;
    }

    if (checkStatus.finishedReason === CheckFinishedReason.STOPPED) {
      return CheckStatusDisplay.IDLE;
    }
    return CheckStatusDisplay.FAILED;
  }

  return CheckStatusDisplay.IDLE;
};
