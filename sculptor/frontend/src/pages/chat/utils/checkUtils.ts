import type { UserConfig } from "../../../api";
import { sendMessageGeneric } from "../../../api";
import type { CheckHistory } from "../Types.ts";

export const restartCheck = async (
  projectID: string,
  taskID: string,
  checkName: string,
  userMessageId: string,
): Promise<void> => {
  await sendMessageGeneric({
    path: { project_id: projectID, task_id: taskID },
    body: {
      message: {
        object_type: "RestartCheckUserMessage",
        check_name: checkName,
        user_message_id: userMessageId,
      },
      is_awaited: false,
    },
  });
};

export const stopCheck = async (
  projectID: string,
  taskID: string,
  checkName: string,
  runId: string,
  userMessageId: string,
): Promise<void> => {
  await sendMessageGeneric({
    path: { project_id: projectID, task_id: taskID },
    body: {
      message: {
        object_type: "StopCheckUserMessage",
        check_name: checkName,
        run_id: runId,
        user_message_id: userMessageId,
      },
      is_awaited: false,
    },
  });
};

export const filterChecksWithCommands = (
  checkNames: Array<string>,
  checkHistoryByName: Record<string, CheckHistory>,
): Array<string> => {
  return checkNames.filter((checkName) => {
    const checkHistory = checkHistoryByName[checkName];
    const latestRunId = checkHistory?.runIds?.[checkHistory.runIds.length - 1];
    const latestStatus = latestRunId ? checkHistory.statusByRunId[latestRunId] : null;
    const fullCheck = latestStatus?.check || checkHistory?.checkDefinition || { name: checkName, command: null };
    return !!fullCheck.command;
  });
};

export const isCheckEnabled = (checkName: string, userConfig: UserConfig): boolean => {
  if (checkName === "Scout") {
    return userConfig.isScoutBetaFeatureOn ?? false;
  }
  return true;
};

export const filterEnabledChecks = (checkNames: Array<string>, userConfig: UserConfig): Array<string> => {
  return checkNames.filter((checkName) => isCheckEnabled(checkName, userConfig));
};
