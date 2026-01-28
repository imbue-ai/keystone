import type { ChatMessage, InsertedChatMessage, TaskUpdate } from "../../api";
import { ChatMessageRole } from "../../api";
import { normalizeTimestamp } from "../../components/TaskItemUtils.ts";
import type {
  CheckHistory,
  CheckOutputList,
  CheckOutputWithSource,
  NewCheckOutputsData,
} from "../../pages/chat/Types.ts";
import { isCheckFinishedRunnerMessage, isCheckLaunchedRunnerMessage, isChecksDefinedRunnerMessage } from "../Guards.ts";

export type ChatMessagesState = {
  completedChatMessages: Array<ChatMessage>;
  inProgressChatMessage: ChatMessage | null;
  queuedChatMessages: Array<ChatMessage>;
  workingUserMessageId: string | null;
};

export const chatMessagesReducer = (currentState: ChatMessagesState, taskUpdate: TaskUpdate): ChatMessagesState => {
  const newChatMessages = taskUpdate.chatMessages || [];
  const insertedMessages = taskUpdate.insertedMessages || [];

  const updatedCompletedMessages = mergeAndDeduplicateMessages(
    currentState.completedChatMessages,
    newChatMessages,
    insertedMessages,
  );

  return {
    completedChatMessages: updatedCompletedMessages,
    inProgressChatMessage: taskUpdate.inProgressChatMessage,
    queuedChatMessages: taskUpdate.queuedChatMessages,
    workingUserMessageId: taskUpdate.inProgressUserMessageId,
  };
};

const insertMessagesIntoHistory = (
  completedMessages: Array<ChatMessage>,
  insertedMessages: Array<InsertedChatMessage>,
): Array<ChatMessage> => {
  if (insertedMessages.length === 0) {
    return completedMessages;
  }

  const insertionsByAfterId = new Map<string, Array<ChatMessage>>();
  for (const { message, afterMessageId } of insertedMessages) {
    if (!insertionsByAfterId.has(afterMessageId)) {
      insertionsByAfterId.set(afterMessageId, []);
    }
    insertionsByAfterId.get(afterMessageId)!.push(message);
  }

  const result: Array<ChatMessage> = [];
  for (const msg of completedMessages) {
    result.push(msg);
    const insertionsAfterThis = insertionsByAfterId.get(msg.id);
    if (insertionsAfterThis) {
      result.push(...insertionsAfterThis);
      insertionsByAfterId.delete(msg.id);
    }
  }

  if (insertionsByAfterId.size > 0) {
    for (const [afterMessageId, messages] of insertionsByAfterId.entries()) {
      console.error(
        `Could not find message with id ${afterMessageId} to insert after. Ignoring ${messages.length} inserted messages.`,
      );
    }
  }

  return result;
};

const mergeAndDeduplicateMessages = (
  currentMessages: Array<ChatMessage>,
  newMessages: Array<ChatMessage>,
  insertedMessages: Array<InsertedChatMessage>,
): Array<ChatMessage> => {
  if (newMessages.length === 0 && insertedMessages.length === 0) {
    return currentMessages;
  }

  const messageById = Object.fromEntries(currentMessages.map((msg) => [msg.id, { ...msg }]));
  for (const msg of newMessages) {
    messageById[msg.id] = { ...msg };
  }

  // Deduplicate messages (we might not need this, but just in case)
  const allUniqueMessageIds: Array<string> = [];
  for (const msg of [...currentMessages, ...newMessages]) {
    if (!allUniqueMessageIds.includes(msg.id)) {
      allUniqueMessageIds.push(msg.id);
    }
  }

  const withNewMessages = allUniqueMessageIds.map((id) => messageById[id]);

  // Roll snapshotId forward from user messages to agent messages
  let lastSnapshotId: string | null = null;
  // eslint-disable-next-line @typescript-eslint/naming-convention
  let lastDidSnapshotFail: boolean | null = null;
  for (const msg of withNewMessages) {
    if (msg.snapshotId && msg.role === ChatMessageRole.USER) {
      lastSnapshotId = msg.snapshotId;
    } else if (lastSnapshotId && msg.role === ChatMessageRole.ASSISTANT) {
      msg.snapshotId = lastSnapshotId;
      lastSnapshotId = null;
    }

    if (msg.didSnapshotFail && msg.role === ChatMessageRole.USER) {
      lastDidSnapshotFail = msg.didSnapshotFail;
    } else if (lastDidSnapshotFail && msg.role === ChatMessageRole.ASSISTANT) {
      msg.didSnapshotFail = lastDidSnapshotFail;
      lastDidSnapshotFail = null;
    }
  }

  // Insert historical messages (fork indicators, etc.)
  return insertMessagesIntoHistory(withNewMessages, insertedMessages);
};

const deduplicateCheckOutputs = (
  existing: Array<CheckOutputWithSource>,
  newOutputs: Array<CheckOutputWithSource>,
): Array<CheckOutputWithSource> => {
  if (newOutputs.length === 0) {
    return existing;
  }

  const existingIds = new Set(existing.map((item) => item.output.id));
  const uniqueNewOutputs = newOutputs.filter((item) => !existingIds.has(item.output.id));
  if (uniqueNewOutputs.length === 0) {
    return existing;
  }

  return [...existing, ...uniqueNewOutputs];
};

const buildCheckOutputsArtifact = (
  checkOutputListByMessageId: Record<string, CheckOutputList>,
): NewCheckOutputsData | null => {
  const checkOutputsByMessageIdEntries = Object.entries(checkOutputListByMessageId).map(
    ([userMessageId, checkOutputList]) => {
      const currentRunIdByCheckName = checkOutputList.currentRunIdByCheckName || {};
      const outputs: Array<CheckOutputWithSource> = [];

      Object.entries(currentRunIdByCheckName).forEach(([checkName, runId]) => {
        const outputsForCheck = checkOutputList.checkOutputsByCheckName?.[checkName] || [];
        outputsForCheck.forEach((item) => {
          if (item.runId === runId) {
            outputs.push(item);
          }
        });
      });

      return [userMessageId, outputs] as const;
    },
  );

  const filteredEntries = checkOutputsByMessageIdEntries.filter(([, outputs]) => outputs.length > 0);
  if (filteredEntries.length === 0) {
    return null;
  }

  return {
    checkOutputsByMessageId: Object.fromEntries(filteredEntries),
  };
};

export type CheckOutputsState = {
  checkOutputListByMessageId: Record<string, CheckOutputList>;
  newCheckOutputs: NewCheckOutputsData | null;
};

export const checkOutputMessagesReducer = (
  currentState: CheckOutputsState,
  taskUpdate: TaskUpdate,
): CheckOutputsState => {
  const rawMessages = taskUpdate.newCheckOutputMessages ?? [];

  if (rawMessages.length === 0) {
    return currentState;
  }

  let nextCheckOutputListByMessageId = { ...currentState.checkOutputListByMessageId };
  let didChange = false;

  rawMessages.forEach((msg) => {
    if (!msg.userMessageId || !msg.checkName || !msg.runId || !Array.isArray(msg.outputEntries)) {
      return;
    }

    const previousList = nextCheckOutputListByMessageId[msg.userMessageId] || {
      checkOutputsByCheckName: {},
      currentRunIdByCheckName: {},
    };

    const previousRunId = previousList.currentRunIdByCheckName?.[msg.checkName];

    const currentRunIdByCheckName = { ...(previousList.currentRunIdByCheckName || {}) };
    currentRunIdByCheckName[msg.checkName] = msg.runId;

    const existingOutputsForCheck = previousList.checkOutputsByCheckName?.[msg.checkName] || [];
    const baseOutputsForCheck = previousRunId === msg.runId ? [...existingOutputsForCheck] : [];

    const taggedOutputs: Array<CheckOutputWithSource> = msg.outputEntries.map((output) => ({
      output,
      runId: msg.runId!,
      checkName: msg.checkName!,
    }));

    const mergedOutputs = deduplicateCheckOutputs(baseOutputsForCheck, taggedOutputs);

    const didOutputsChange =
      mergedOutputs.length !== existingOutputsForCheck.length ||
      mergedOutputs.some((item) => {
        return !existingOutputsForCheck.some((existing) => existing.output.id === item.output.id);
      });

    const checkOutputsByCheckName = {
      ...(previousList.checkOutputsByCheckName || {}),
      [msg.checkName]: mergedOutputs,
    };

    const didRunChange = previousRunId !== msg.runId;

    if (didOutputsChange || didRunChange) {
      didChange = true;
    }

    nextCheckOutputListByMessageId = {
      ...nextCheckOutputListByMessageId,
      [msg.userMessageId]: {
        checkOutputsByCheckName,
        currentRunIdByCheckName,
      },
    };
  });

  if (!didChange) {
    return currentState;
  }

  return {
    checkOutputListByMessageId: nextCheckOutputListByMessageId,
    newCheckOutputs: buildCheckOutputsArtifact(nextCheckOutputListByMessageId),
  };
};

export type ChecksState = {
  checksData: Record<string, Record<string, CheckHistory>>;
  checksDefinedForMessage: Set<string>;
};

export const checkUpdateMessagesReducer = (currentState: ChecksState, taskUpdate: TaskUpdate): ChecksState => {
  const checkUpdateMessages = taskUpdate.checkUpdateMessages || [];

  let updatedChecksData = { ...currentState.checksData };
  const updatedChecksDefinedForMessage = new Set(currentState.checksDefinedForMessage);

  checkUpdateMessages.forEach((msg) => {
    if (isChecksDefinedRunnerMessage(msg)) {
      const prevCheckHistoryByName = updatedChecksData[msg.userMessageId!] || {};
      const newHistoryByName = Object.fromEntries(
        Object.keys(msg.checkByName).map((checkName) => {
          const newCheckHistory = { ...(prevCheckHistoryByName[checkName] || {}) };
          newCheckHistory.statusByRunId = { ...(newCheckHistory.statusByRunId || {}) };
          newCheckHistory.runIds = [...(newCheckHistory.runIds || [])];
          newCheckHistory.checkDefinition = msg.checkByName[checkName];
          return [checkName, newCheckHistory];
        }),
      );
      updatedChecksData = { ...updatedChecksData, [msg.userMessageId!]: newHistoryByName };

      if (msg.userMessageId) {
        updatedChecksDefinedForMessage.add(msg.userMessageId);
      }
    } else if (isCheckLaunchedRunnerMessage(msg)) {
      const prevHistoryByName = updatedChecksData[msg.userMessageId!] || {};
      const prevHistory = prevHistoryByName[msg.check.name] || {
        statusByRunId: {},
        runIds: [],
      };
      const newStatusByRunId = { ...(prevHistory.statusByRunId || {}) };
      newStatusByRunId[msg.runId] = {
        check: msg.check,
        startedAt: normalizeTimestamp(msg.approximateCreationTime!),
      };
      const newHistory = { ...prevHistory, statusByRunId: newStatusByRunId };
      if (newHistory.runIds.indexOf(msg.runId) === -1) {
        newHistory.runIds = [...newHistory.runIds, msg.runId];
      }
      const newHistoryByName = { ...prevHistoryByName, [msg.check.name]: newHistory };
      updatedChecksData = {
        ...updatedChecksData,
        [msg.userMessageId!]: newHistoryByName,
      };
    } else if (isCheckFinishedRunnerMessage(msg)) {
      const prevHistoryByName = updatedChecksData[msg.userMessageId!] || {};
      const prevHistory = prevHistoryByName[msg.check.name] || {
        statusByRunId: {},
        runIds: [],
      };
      const newStatusByRunId = { ...(prevHistory.statusByRunId || {}) };
      newStatusByRunId[msg.runId] = {
        stoppedAt: normalizeTimestamp(msg.approximateCreationTime!),
        exitCode: msg.exitCode,
        finishedReason: msg.finishedReason,
        archivalReason: msg.archivalReason,
        check: newStatusByRunId[msg.runId]?.check || msg.check,
        startedAt: newStatusByRunId[msg.runId]?.startedAt,
      };
      const newHistory = { ...prevHistory, statusByRunId: newStatusByRunId };
      if (newHistory.runIds.indexOf(msg.runId) === -1) {
        newHistory.runIds = [...newHistory.runIds, msg.runId];
      }
      const newHistoryByName = { ...prevHistoryByName, [msg.check.name]: newHistory };
      updatedChecksData = {
        ...updatedChecksData,
        [msg.userMessageId!]: newHistoryByName,
      };
    }
  });

  return {
    checksData: updatedChecksData,
    checksDefinedForMessage: updatedChecksDefinedForMessage,
  };
};

export type LogsState = {
  logs: Array<string>;
};

export const logsReducer = (currentState: LogsState, taskUpdate: TaskUpdate): LogsState => {
  const newLogs = taskUpdate.logs || [];

  if (newLogs.length === 0) {
    return currentState;
  }

  return {
    logs: [...currentState.logs, ...newLogs],
  };
};

export type FeedbackState = {
  feedbackByMessageId: Record<string, string>;
};

export const feedbackReducer = (currentState: FeedbackState, taskUpdate: TaskUpdate): FeedbackState => {
  const newFeedback = taskUpdate.feedbackByMessageId;

  if (!newFeedback) {
    return currentState;
  }

  return {
    feedbackByMessageId: newFeedback,
  };
};
