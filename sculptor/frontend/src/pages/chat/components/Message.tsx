import { Box, Flex, IconButton, Text } from "@radix-ui/themes";
import { useSetAtom } from "jotai";
import { CopyIcon } from "lucide-react";
import type { ReactElement } from "react";
import { useEffect, useState } from "react";

import { FilePreviewList } from "~/components/FilePreviewList.tsx";

import type {
  ContextSummaryBlock,
  ErrorBlock,
  FileBlock,
  ForkedFromBlock,
  ForkedToBlock,
  ResumeResponseBlock,
  TextBlock,
  ToolResultBlock,
  ToolUseBlock,
  WarningBlock,
} from "../../../api";
import { type ChatMessage } from "../../../api";
import { ChatMessageRole, ElementIds, sendMessageGeneric, TaskStatus } from "../../../api";
import type { BlockUnion } from "../../../common/Guards.ts";
import {
  isCommandBlock,
  isContextSummaryBlock,
  isErrorBlock,
  isFileBlock,
  isForkedFromBlock,
  isForkedToBlock,
  isResumeResponseBlock,
  isTextBlock,
  isToolResultBlock,
  isToolUseBlock,
  isWarningBlock,
} from "../../../common/Guards.ts";
import { useImbueParams } from "../../../common/NavigateUtils.ts";
import { taskModalMessageIDAtom, TaskModalMode, taskModalModeAtom } from "../../../common/state/atoms/taskModal.ts";
import { useTaskModal } from "../../../common/state/hooks/useTaskModal.ts";
import { MarkdownBlock } from "../../../components/MarkdownBlock";
import { TipTapViewer } from "../../../components/TipTapViewer";
import type { ToastContent } from "../../../components/Toast.tsx";
import { Toast, ToastType } from "../../../components/Toast.tsx";
import type { CheckHistory } from "../Types.ts";
import { getElementIdForMessage } from "../utils/utils.ts";
import { ContextSummary } from "./ContextSummary.tsx";
import {
  FeedbackDialog,
  type FeedbackIssueType,
  FeedbackType,
  type FeedbackType as FeedbackTypeType,
} from "./FeedbackDialog";
import { ForkedFromBlockComponent, ForkedToBlockComponent } from "./ForkedBlocks.tsx";
import styles from "./Message.module.scss";
import { MessageActionBar } from "./MessageActionBar.tsx";
import { MessageChecks } from "./MessageChecks.tsx";
import { SystemErrorBlock } from "./SystemErrorBlock";
import { SystemWarningBlock } from "./SystemWarningBlock";
import { CollapsibleToolSection } from "./tools/ToolComponents.tsx";

type MessageProps = {
  message: ChatMessage;
  isStreaming: boolean;
  retryLastUserMessage: () => void;
  isLastMessage: boolean;
  taskStatus: TaskStatus;
  isLastAssistantMessage?: boolean;
  checksData?: Record<string, Record<string, CheckHistory>>;
  onShowChecks?: () => void;
  userMessageId?: string;
  submittedFeedback?: string | null;
};

type RenderGroup =
  | { type: "text"; blocks: Array<TextBlock> }
  | { type: "tools"; blocks: Array<ToolUseBlock | ToolResultBlock> }
  | { type: "error"; block: ErrorBlock }
  | { type: "warning"; block: WarningBlock }
  | { type: "context_summary"; block: ContextSummaryBlock }
  | { type: "resume_response"; block: ResumeResponseBlock }
  | { type: "forked_from"; block: ForkedFromBlock }
  | { type: "forked_to"; block: ForkedToBlock };

// Renders either a user or assistant message based on the role

export const Message = ({
  message,
  isStreaming = false,
  isLastMessage,
  retryLastUserMessage,
  taskStatus,
  isLastAssistantMessage = false,
  checksData,
  onShowChecks,
  userMessageId,
  submittedFeedback,
}: MessageProps): ReactElement => {
  if (message.role === ChatMessageRole.USER) {
    return <UserMessage message={message} />;
  } else if (message.role === ChatMessageRole.ASSISTANT) {
    return (
      <AssistantMessage
        message={message}
        isStreaming={isStreaming}
        isLastMessage={isLastMessage}
        retryLastUserMessage={retryLastUserMessage}
        taskStatus={taskStatus}
        isLastAssistantMessage={isLastAssistantMessage}
        checksData={checksData}
        onShowChecks={onShowChecks}
        userMessageId={userMessageId}
        submittedFeedback={submittedFeedback}
        isLastMessageOverall={isLastMessage && isLastAssistantMessage}
      />
    );
  } else {
    throw new Error(`Unknown message role: ${message.role}`);
  }
};

type AssistantMessageProps = {
  message: ChatMessage;
  isStreaming: boolean;
  isLastMessage: boolean;
  retryLastUserMessage: () => void;
  taskStatus: TaskStatus;
  isLastAssistantMessage?: boolean;
  checksData?: Record<string, Record<string, CheckHistory>>;
  onShowChecks?: () => void;
  userMessageId?: string;
  submittedFeedback?: string | null;
  isLastMessageOverall?: boolean;
};
const AssistantMessage = ({
  message,
  isStreaming = false,
  isLastMessage,
  retryLastUserMessage,
  taskStatus,
  isLastAssistantMessage = false,
  checksData,
  onShowChecks,
  userMessageId,
  submittedFeedback = null,
  isLastMessageOverall = false,
}: AssistantMessageProps): ReactElement => {
  const { projectID, taskID } = useImbueParams();
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [selectedFeedbackType, setSelectedFeedbackType] = useState<FeedbackTypeType>(FeedbackType.POSITIVE);
  const [localSubmittedFeedback, setLocalSubmittedFeedback] = useState<FeedbackTypeType | null>(
    submittedFeedback as FeedbackTypeType | null,
  );
  const setTaskModalMode = useSetAtom(taskModalModeAtom);
  const setTaskModalMessageID = useSetAtom(taskModalMessageIDAtom);
  const [toast, setToast] = useState<ToastContent | null>(null);
  const { showTaskModal } = useTaskModal();

  useEffect(() => {
    if (submittedFeedback !== null && submittedFeedback !== localSubmittedFeedback) {
      setLocalSubmittedFeedback(submittedFeedback as FeedbackTypeType);
    }
  }, [submittedFeedback, message.id, localSubmittedFeedback]);

  // Handle feedback button clicks
  const handleThumbsUp = (): void => {
    if (localSubmittedFeedback === FeedbackType.POSITIVE) {
      handleFeedbackRemoval();
      return;
    }
    setSelectedFeedbackType(FeedbackType.POSITIVE);
    setIsDialogOpen(true);
  };

  const handleThumbsDown = (): void => {
    if (localSubmittedFeedback === FeedbackType.NEGATIVE) {
      handleFeedbackRemoval();
      return;
    }
    setSelectedFeedbackType(FeedbackType.NEGATIVE);
    setIsDialogOpen(true);
  };

  const handleFeedbackRemoval = async (): Promise<void> => {
    if (!projectID || !taskID) {
      console.error("Missing project ID or task ID for feedback removal");
      return;
    }

    try {
      await sendMessageGeneric({
        path: { project_id: projectID, task_id: taskID },
        body: {
          message: {
            object_type: "MessageFeedbackUserMessage",
            feedback_message_id: message.id,
            feedback_type: "none",
            comment: null,
            issue_type: null,
          },
        },
      });
      setLocalSubmittedFeedback(null);
    } catch (error) {
      console.error("Failed to remove feedback:", error);
    }
  };

  const handleFeedbackSubmit = async (
    feedbackType: FeedbackTypeType,
    comment: string,
    issueType?: FeedbackIssueType,
  ): Promise<void> => {
    if (!projectID || !taskID) {
      console.error("Missing project ID or task ID for feedback submission");
      return;
    }

    try {
      await sendMessageGeneric({
        path: { project_id: projectID, task_id: taskID },
        body: {
          message: {
            object_type: "MessageFeedbackUserMessage",
            feedback_message_id: message.id,
            feedback_type: feedbackType,
            comment: comment || null,
            issue_type: issueType || null,
          },
        },
      });
      setLocalSubmittedFeedback(feedbackType);
    } catch (error) {
      console.error("Failed to submit feedback:", error);
    }
  };

  const renderGroups: Array<RenderGroup> = [];
  let currentTextBlocks: Array<TextBlock> = [];
  let currentToolBlocks: Array<ToolUseBlock | ToolResultBlock> = [];

  const handleFork = (): void => {
    setTaskModalMode(TaskModalMode.FORK_TASK);
    setTaskModalMessageID(message.id);
    showTaskModal();
  };

  const handleCopy = (): void => {
    const content = message.content
      .filter((block) => isTextBlock(block))
      .map((block) => {
        return block.text;
      })
      .join("");

    if (!content) {
      return;
    }
    navigator.clipboard.writeText(content);
    setToast({
      title: "Message copied to clipboard",
      type: ToastType.SUCCESS,
    });
  };

  // Check if this message contains initial setup script content
  const isSetupScriptMessage = (): boolean => {
    return message.content.some((block) => {
      if (block.type === "tool_use") {
        // Check if this is marked as an automated setup script
        return block.input?.is_automated_command === true;
      }
      return false;
    });
  };

  const flushCurrentGroup = (): void => {
    if (currentTextBlocks.length > 0) {
      renderGroups.push({ type: "text", blocks: currentTextBlocks });
      currentTextBlocks = [];
    }

    if (currentToolBlocks.length > 0) {
      renderGroups.push({ type: "tools", blocks: currentToolBlocks });
      currentToolBlocks = [];
    }
  };

  for (const contentBlock of message.content) {
    if (isTextBlock(contentBlock)) {
      if (currentToolBlocks.length > 0) {
        renderGroups.push({ type: "tools", blocks: currentToolBlocks });
        currentToolBlocks = [];
      }
      currentTextBlocks.push(contentBlock);
    } else if (isToolUseBlock(contentBlock) || isToolResultBlock(contentBlock)) {
      if (currentTextBlocks.length > 0) {
        renderGroups.push({ type: "text", blocks: currentTextBlocks });
        currentTextBlocks = [];
      }
      currentToolBlocks.push(contentBlock);
    } else if (isErrorBlock(contentBlock)) {
      flushCurrentGroup();
      renderGroups.push({ type: "error", block: contentBlock });
    } else if (isWarningBlock(contentBlock)) {
      flushCurrentGroup();
      renderGroups.push({ type: "warning", block: contentBlock });
    } else if (isCommandBlock(contentBlock)) {
      flushCurrentGroup();
    } else if (isContextSummaryBlock(contentBlock)) {
      flushCurrentGroup();
      renderGroups.push({ type: "context_summary", block: contentBlock });
    } else if (isResumeResponseBlock(contentBlock)) {
      flushCurrentGroup();
      renderGroups.push({ type: "resume_response", block: contentBlock });
      // ignoring command block until we have a better design for it
    } else if (isForkedToBlock(contentBlock)) {
      flushCurrentGroup();
      renderGroups.push({ type: "forked_to", block: contentBlock });
    } else if (isForkedFromBlock(contentBlock)) {
      flushCurrentGroup();
      renderGroups.push({ type: "forked_from", block: contentBlock });
    } else {
      throw new Error(`Unknown content block type: ${JSON.stringify(contentBlock)}`);
    }
  }

  flushCurrentGroup();

  const hasAgentResponse = renderGroups.some((group) => group.type === "text" || group.type === "tools"); // some groups are only errors + warnings

  return (
    <>
      <Box width="100%" maxWidth="100%" px="4" className={styles.assistantMessage} id={getElementIdForMessage(message)}>
        <Flex direction="column" className={styles.assistantMessageContent} data-testid={ElementIds.ASSISTANT_MESSAGE}>
          {renderGroups.map((group, index) => {
            if (group.type === "text") {
              return <MergedTextBlock key={`text-${index}`} textBlocks={group.blocks} />;
            } else if (group.type === "tools") {
              const isActiveToolGroup = isStreaming && index === renderGroups.length - 1;
              return (
                <CollapsibleToolSection key={`tools-${index}`} toolBlocks={group.blocks} isActive={isActiveToolGroup} />
              );
            } else if (group.type === "error") {
              const shouldShowRetryButton =
                isLastMessage && taskStatus !== TaskStatus.ERROR && index === renderGroups.length - 1;
              return (
                <SystemErrorBlock
                  key={`error-${index}`}
                  errorType={group.block.errorType}
                  content={group.block.traceback}
                  message={group.block.message}
                  messageId={message.id}
                  taskStatus={taskStatus}
                  onRetryRequest={retryLastUserMessage}
                  showRetryButton={shouldShowRetryButton}
                />
              );
            } else if (group.type === "warning") {
              return (
                <SystemWarningBlock
                  key={`warning-${index}`}
                  warningType={group.block.warningType ?? "Warning"}
                  content={group.block.traceback}
                  message={group.block.message}
                />
              );
            } else if (group.type === "context_summary") {
              // Render context summary as a collapsible section similar to tools
              return <ContextSummary key={`context_summary-${index}`} message={group.block.text} />;
            } else if (group.type === "resume_response") {
              // Render something subtle to indicate that the agent is resuming after being shut down
              return (
                <Box key={`context_summary-${index}`} maxWidth="100%" data-testid={ElementIds.RESUME_RESPONSE}>
                  <Flex direction="column" maxWidth="100%">
                    <Flex align="center" gap="2" py="1" maxWidth="100%">
                      <Text size="2">Resumed agent response</Text>
                    </Flex>
                  </Flex>
                </Box>
              );
            } else if (group.type === "forked_to") {
              return <ForkedToBlockComponent key={`forked_to-${index}`} forkedToTaskId={group.block.forkedToTaskId} />;
            } else if (group.type === "forked_from") {
              return (
                <ForkedFromBlockComponent
                  key={`forked_from-${index}`}
                  forkedFromTaskId={group.block.forkedFromTaskId}
                />
              );
            }
            return null;
          })}
          {(!isLastAssistantMessage || !isStreaming) && hasAgentResponse && !isSetupScriptMessage() && (
            <Flex direction="row" gap="2" align="center" justify="between">
              <MessageActionBar
                onThumbsUp={handleThumbsUp}
                onThumbsDown={handleThumbsDown}
                onFork={handleFork}
                onCopy={handleCopy}
                submittedFeedback={localSubmittedFeedback}
                snapshotId={message.snapshotId}
                didSnapshotFail={message.didSnapshotFail}
              />
              <MessageChecks
                messageId={message.id}
                messageRole={message.role}
                checksData={checksData}
                isLastAssistantMessage={isLastAssistantMessage}
                onShowChecks={onShowChecks}
                userMessageId={userMessageId}
                isLastMessageOverall={isLastMessageOverall}
              />
            </Flex>
          )}
        </Flex>

        <FeedbackDialog
          isOpen={isDialogOpen}
          onClose={() => setIsDialogOpen(false)}
          onSubmit={handleFeedbackSubmit}
          feedbackType={selectedFeedbackType}
        />
      </Box>
      <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} title={toast?.title} type={toast?.type} />
    </>
  );
};

type UserMessageProps = {
  message: ChatMessage;
};

const UserMessage = ({ message }: UserMessageProps): ReactElement => {
  const [toast, setToast] = useState<ToastContent | null>(null);
  const textBlocks = message.content.filter((block: BlockUnion): block is TextBlock => isTextBlock(block));
  const fileBlocks = message.content.filter((block: BlockUnion): block is FileBlock => isFileBlock(block));

  if (textBlocks.length === 0 && fileBlocks.length === 0) {
    return <></>;
  }

  const handleCopy = (): void => {
    const content = textBlocks.map((block: TextBlock): string => block.text).join("");
    navigator.clipboard.writeText(content);
    setToast({
      title: "Message copied to clipboard",
      type: ToastType.SUCCESS,
    });
  };

  return (
    <>
      <Box width="100%" mt="6" id={getElementIdForMessage(message)}>
        <Box px="4" className={styles.userMessage} data-testid={ElementIds.USER_MESSAGE} position="relative">
          {textBlocks.map(
            (block: TextBlock, index: number): ReactElement => (
              <TipTapViewer content={block.text} key={index} />
            ),
          )}
          {fileBlocks.length > 0 && (
            <Box mb="3" ml="-2">
              <FilePreviewList files={fileBlocks.map((block) => block.source)} />
            </Box>
          )}
          <IconButton
            variant="ghost"
            size="1"
            onClick={handleCopy}
            className={styles.userMessageCopyButton}
            title="Copy message"
            style={{
              position: "absolute",
              bottom: "var(--space-2)",
              right: "var(--space-2)",
            }}
          >
            <CopyIcon size={16} />
          </IconButton>
        </Box>
      </Box>
      <Toast
        open={!!toast}
        onOpenChange={(open: boolean): void => {
          if (!open) setToast(null);
        }}
        title={toast?.title}
        type={toast?.type}
      />
    </>
  );
};

const MergedTextBlock = ({ textBlocks }: { textBlocks: Array<TextBlock> }): ReactElement => {
  const mergedText = textBlocks.map((block: TextBlock) => block.text).join("");
  return <MarkdownBlock content={mergedText} />;
};
