import { Box, Flex, Link, ScrollArea, Spinner, Text } from "@radix-ui/themes";
import { last } from "lodash";
import type { ReactElement } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useScrollPersistence } from "~/pages/chat/hooks/useScrollPersistence.ts";
import {
  useSmoothStreamingOnTaskSwitch,
  useSmoothStreamingViewportObserver,
} from "~/pages/chat/hooks/useSmoothStreamingViewportObserver.ts";

import { type ChatMessage, type CodingAgentTaskView } from "../../../api";
import { archiveTask, ChatMessageRole, ElementIds, LlmModel, restoreTask, sendMessage, TaskStatus } from "../../../api";
import {
  CHAT_INPUT_ELEMENT_ID,
  MAX_CONTEXT_WINDOW_TOKENS_BEFORE_FORCED_COMPACT_BY_MODEL,
} from "../../../common/Constants.ts";
import { isForkedFromBlock, isForkedToBlock } from "../../../common/Guards.ts";
import { useImbueParams } from "../../../common/NavigateUtils.ts";
import { useTask } from "../../../common/state/hooks/useTaskHelpers.ts";
import { MarkdownBlock } from "../../../components/MarkdownBlock.tsx";
import { PulsingCircle } from "../../../components/PulsingCircle.tsx";
import { Toast, type ToastContent, ToastType } from "../../../components/Toast.tsx";
import type { CheckHistory, SuggestionsData } from "../Types.ts";
import { getElementIdForMessage } from "../utils/utils.ts";
import { ChatInput } from "./ChatInput";
import styles from "./ChatInterface.module.scss";
import { ChatBuildingSkeleton, ChatInterfaceSkeleton } from "./ChatInterfaceSkeleton.tsx";
import { Message } from "./Message.tsx";
import { QueuedMessageCard } from "./QueuedMessageCard.tsx";
import { ThinkingIndicator } from "./ThinkingIndicator";

type ArchiveInputProps = {
  projectId: string;
  taskId: string;
};

const ArchiveInput = ({ projectId, taskId }: ArchiveInputProps): ReactElement => {
  const [toast, setToast] = useState<ToastContent | null>(null);
  const [isUnarchiving, setIsUnarchiving] = useState(false);

  const onUnarchive = async (): Promise<void> => {
    setIsUnarchiving(true);
    try {
      await archiveTask({
        path: { project_id: projectId, task_id: taskId },
        body: { isArchived: false },
      });
    } catch (error) {
      console.error("Failed to unarchive task:", error);
      setToast({ title: "Failed to unarchive task", type: ToastType.ERROR });
    } finally {
      setIsUnarchiving(false);
    }
  };

  return (
    <>
      <Flex px="4" py="3" gapX="2" className={styles.archiveBox} align="center" justify="center">
        <Text>This task was archived. </Text>
        {isUnarchiving ? (
          <Flex gapX="2" align="center">
            <Spinner size="1" />
            <Text>Unarchiving...</Text>
          </Flex>
        ) : (
          <Link onClick={() => onUnarchive()}>Unarchive Task</Link>
        )}
      </Flex>
      <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} title={toast?.title} type={toast?.type} />
    </>
  );
};

type ErrorInputProps = {
  projectId: string;
  taskId: string;
};

const ErrorInput = ({ projectId, taskId }: ErrorInputProps): ReactElement => {
  const [toast, setToast] = useState<ToastContent | null>(null);

  const onRestore = async (): Promise<void> => {
    try {
      await restoreTask({
        path: { project_id: projectId, task_id: taskId },
      });
    } catch (error) {
      console.error("Failed to restore task:", error);
      setToast({ title: "Failed to restore task", type: ToastType.ERROR });
    }
  };

  return (
    <>
      <Flex px="4" py="3" gap="1" className={styles.archiveBox} align="center" justify="center" wrap="wrap">
        <Text>The agent is in an error state. </Text>
        <Link onClick={() => onRestore()}>Click here to try to restore the agent.</Link>
      </Flex>
      <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} title={toast?.title} type={toast?.type} />
    </>
  );
};

const getLastUserMessageByType = (messages: Array<ChatMessage>, type: ChatMessageRole): ChatMessage | undefined => {
  const filtered = messages.filter((message) => message.role === type);
  return last(filtered);
};

const getLastUserMessageId = (messages: Array<ChatMessage>): string | undefined => {
  return getLastUserMessageByType(messages, ChatMessageRole.USER)?.id;
};

const getHeightOfInnerContainer = (groupedMessages: Array<ChatMessage>): number => {
  const lastUserMessage = getLastUserMessageByType(groupedMessages, ChatMessageRole.USER);

  if (!lastUserMessage) {
    return 0;
  }

  let height = 0;

  // Get all assistant messages after the last user message
  const lastUserMessageIndex = groupedMessages.findIndex((msg) => msg.id === lastUserMessage.id);
  const assistantMessagesAfterUser = groupedMessages
    .slice(lastUserMessageIndex + 1)
    .filter((msg) => msg.role === ChatMessageRole.ASSISTANT);

  // Calculate height of user message
  const userMessageElement = document.getElementById(getElementIdForMessage(lastUserMessage));
  height += userMessageElement?.getBoundingClientRect().height ?? 0;

  // Calculate combined height of all assistant messages after the last user message
  assistantMessagesAfterUser.forEach((assistantMessage) => {
    const assistantMessageElement = document.getElementById(getElementIdForMessage(assistantMessage));
    height += assistantMessageElement?.getBoundingClientRect().height ?? 0;
  });

  // Add heights of UI elements
  const chatInputElement = document.getElementById(CHAT_INPUT_ELEMENT_ID);
  height += chatInputElement?.getBoundingClientRect().height ?? 0;
  // Always add space for ThinkingIndicator container (64px)
  height += 64;

  // Add fixed spacing: bottom padding + header + margin top
  height += 16 + 48 + 32 * 2;

  return height;
};

type ChatInterfaceProps = {
  chatMessages: Array<ChatMessage>;
  queuedChatMessages: Array<ChatMessage>;
  workingUserMessageId: string | null;
  isStreaming: boolean;
  tokensUsed: number;
  onClickLogsWhileBuilding?: () => void;
  checksData?: Record<string, Record<string, CheckHistory>>;
  checksDefinedForMessage?: Set<string>;
  suggestionsData?: SuggestionsData;
  onShowSuggestions?: () => void;
  onShowChecks?: () => void;
  appendTextRef?: React.MutableRefObject<((text: string) => void) | null>;
  feedbackByMessageId?: Record<string, string>;
  onFlashTab?: (tabId: string) => void;
};

export const ChatInterface = ({
  chatMessages,
  isStreaming,
  workingUserMessageId,
  queuedChatMessages,
  tokensUsed,
  onClickLogsWhileBuilding,
  checksData,
  checksDefinedForMessage: _checksDefinedForMessage,
  suggestionsData,
  onShowSuggestions,
  onShowChecks,
  appendTextRef,
  feedbackByMessageId,
  onFlashTab,
}: ChatInterfaceProps): ReactElement => {
  const { projectID, taskID } = useImbueParams();
  const [toast, setToast] = useState<ToastContent | null>(null);

  if (!projectID) {
    throw new Error("Expected projectID to be defined");
  }

  if (!taskID) {
    throw new Error("Expected taskID to be defined");
  }

  const scrollAreaRef = useScrollPersistence(taskID);
  const task = useTask(taskID);
  const bottomSentinelRef = useSmoothStreamingViewportObserver();
  useSmoothStreamingOnTaskSwitch(taskID, bottomSentinelRef);

  const lastUserMessageIdRef = useRef<string | undefined>(getLastUserMessageId(chatMessages));
  const lastTaskIdRef = useRef<string>(taskID);

  const handleRetryLastUserMessage = useCallback(async (): Promise<void> => {
    // Find the last user message
    const userMessages = chatMessages.filter((msg) => msg.role === ChatMessageRole.USER);
    const lastUserMessage = userMessages[userMessages.length - 1];

    if (lastUserMessage && task) {
      // Extract text content from the message
      const messageText = lastUserMessage.content
        .filter((block) => block.type === "text")
        .map((block) => block.text)
        .join("");

      if (messageText) {
        try {
          await sendMessage({
            path: { project_id: projectID, task_id: task.id },
            body: { message: messageText, model: (task.model as LlmModel) || LlmModel.CLAUDE_4_SONNET },
          });
        } catch (error) {
          console.error("Failed to retry message:", error);
          setToast({ title: "Failed to retry message", type: ToastType.ERROR });
        }
      }
    }
  }, [chatMessages, task, projectID]);

  // Auto-scroll to last user message when a new one is added
  useEffect(() => {
    // Don't scroll if we've *just* switched tasks
    if (lastTaskIdRef.current !== taskID) {
      lastTaskIdRef.current = taskID;
      lastUserMessageIdRef.current = getLastUserMessageId(chatMessages);
      return;
    }

    const id = getLastUserMessageId(chatMessages);
    if (lastUserMessageIdRef.current !== id && id) {
      // Use requestAnimationFrame to ensure layout is complete
      const lastUserMessage = chatMessages.find((msg) => msg.id === id);
      if (lastUserMessage) {
        const lastUserMessageElement = document.getElementById(getElementIdForMessage(lastUserMessage));
        lastUserMessageElement?.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      lastUserMessageIdRef.current = id;
    }
  }, [chatMessages, taskID]);

  const height = getHeightOfInnerContainer(chatMessages);

  if (!task) {
    return <ChatInterfaceSkeleton />;
  }

  const maxContextTokensBeforeCompact =
    MAX_CONTEXT_WINDOW_TOKENS_BEFORE_FORCED_COMPACT_BY_MODEL[(task.model as LlmModel) || LlmModel.CLAUDE_4_SONNET];
  const lastAssistantMessageIndex = chatMessages.reduceRight((acc, msg, idx) => {
    return acc === -1 && msg.role === ChatMessageRole.ASSISTANT ? idx : acc;
  }, -1);

  return (
    <>
      <Flex
        direction="column"
        className={styles.mainContent}
        pb="4"
        align="center"
        justify="center"
        width="100%"
        position="relative"
        data-testid={ElementIds.CHAT_PANEL}
        data-is-streaming={task.status === "RUNNING" ? "true" : "false"}
        data-taskid={taskID}
        data-number-of-snapshots={task.numberOfSnapshots}
      >
        <ScrollArea ref={scrollAreaRef} className={styles.messageArea}>
          <div className={styles.spacer} />
          <Flex direction="column" maxWidth="100%" className={styles.messageContainer}>
            <WelcomeMessage task={task} />
            {chatMessages.map((message, index) => {
              const isLastMessage = index === chatMessages.length - 1;
              const isLastAssistantMessage =
                message.role === ChatMessageRole.ASSISTANT && index === lastAssistantMessageIndex;

              let precedingUserMessageId: string | undefined;
              if (message.role === ChatMessageRole.ASSISTANT) {
                for (let i = index - 1; i >= 0; i--) {
                  if (chatMessages[i].role === ChatMessageRole.USER) {
                    precedingUserMessageId = chatMessages[i].id;
                    break;
                  }
                }
              }

              const isForkBlock = isForkBlockMessage(message);
              return (
                <div
                  key={`${message.id}-${index}`}
                  data-testid={ElementIds.CHAT_PANEL_MESSAGE}
                  className={isForkBlock ? styles.forkBlockMessage : styles.regularMessage}
                >
                  <Message
                    message={message}
                    isStreaming={isStreaming && isLastAssistantMessage}
                    retryLastUserMessage={handleRetryLastUserMessage}
                    isLastMessage={isLastMessage}
                    taskStatus={task.status}
                    isLastAssistantMessage={isLastAssistantMessage}
                    checksData={checksData}
                    onShowChecks={onShowChecks}
                    userMessageId={precedingUserMessageId}
                    submittedFeedback={feedbackByMessageId?.[message.id] || null}
                  />
                </div>
              );
            })}
            <div
              ref={bottomSentinelRef}
              aria-hidden="true"
              style={{
                position: "relative",
                height: "1px",
                width: "100%",
                pointerEvents: "none",
                opacity: 0,
                bottom: "30px",
              }}
            />
            {task.status === "BUILDING" && <ChatBuildingSkeleton onViewLogsClick={onClickLogsWhileBuilding} />}
            <div style={{ minHeight: "64px" }}>
              {((task.status === "RUNNING" && workingUserMessageId !== null) || isStreaming) && <ThinkingIndicator />}
              {task.isCompacting && (
                <Flex align="center" justify="start" gap="9px" height="50px" py="3" px="4">
                  <PulsingCircle />
                  <Text size="2">Compacting...</Text>
                </Flex>
              )}
            </div>
            {height > 0 && <div style={{ minHeight: `calc(100dvh - ${height}px)` }} />}
          </Flex>
        </ScrollArea>
        {queuedChatMessages.map((queuedChatMessage: ChatMessage) => (
          <QueuedMessageCard key={queuedChatMessage.id} message={queuedChatMessage} />
        ))}
        {task.isArchived && <ArchiveInput projectId={projectID} taskId={task.id} />}
        {!task.isArchived && task.status !== TaskStatus.ERROR && (
          <ChatInput
            systemPrompt={task.systemPrompt ?? ""}
            model={(task.model as LlmModel) || LlmModel.CLAUDE_4_SONNET}
            isDisabled={queuedChatMessages.length > 0 || tokensUsed >= maxContextTokensBeforeCompact}
            insufficientTokens={tokensUsed >= maxContextTokensBeforeCompact}
            suggestionsData={suggestionsData}
            onShowSuggestions={onShowSuggestions}
            chatMessages={chatMessages}
            appendTextRef={appendTextRef}
            checksData={checksData}
            onFlashTab={onFlashTab}
          />
        )}
        {task.status === TaskStatus.ERROR && <ErrorInput projectId={projectID} taskId={task.id} />}
      </Flex>
      <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} title={toast?.title} type={toast?.type} />
    </>
  );
};

type WelcomeMessageProps = {
  task: CodingAgentTaskView;
};

const WelcomeMessage = ({ task }: WelcomeMessageProps): ReactElement => {
  const branchName = task.branchName;
  const welcomeMessageString = `
  👋 Hi! I’m running in a safe container with a copy of your repo. My branch lives in the container.\n
  Use **Pairing Mode** to bring my branch into your IDE: It checks out my branch ${branchName ? "`" + branchName + "`" : ""} locally and keeps our files + git state synced, so you can instantly test and commit my changes while I see your edits.\n
  When you're ready, turn off Pairing Mode and use the **Merge** panel to merge my branch from my remote container into your local repo. If you expect conflicts, push local → my branch, then ask me to resolve them.
  `.trim();

  return (
    <Box mt="3" mb="-15px">
      <MarkdownBlock content={welcomeMessageString} />
    </Box>
  );
};

const isForkBlockMessage = (message: ChatMessage): boolean => {
  if (message.role !== ChatMessageRole.ASSISTANT) {
    return false;
  }

  if (message.content.length === 0) {
    return false;
  }

  return message.content.every((block) => isForkedFromBlock(block) || isForkedToBlock(block));
};
