import { Box, IconButton, Spinner, Text } from "@radix-ui/themes";
import { CopyIcon, Trash2 } from "lucide-react";
import { type ReactElement, useState } from "react";

import type { ChatMessage, FileBlock, TextBlock } from "../../../api";
import { deleteMessage, ElementIds } from "../../../api";
import type { BlockUnion } from "../../../common/Guards.ts";
import { isFileBlock, isTextBlock } from "../../../common/Guards.ts";
import { useImbueParams } from "../../../common/NavigateUtils.ts";
import { FilePreviewList } from "../../../components/FilePreviewList.tsx";
import { TipTapViewer } from "../../../components/TipTapViewer";
import { Toast, type ToastContent, ToastType } from "../../../components/Toast.tsx";
import styles from "./QueuedMessageCard.module.scss";

type QueuedMessageCardProps = {
  message: ChatMessage;
};

export const QueuedMessageCard = ({ message }: QueuedMessageCardProps): ReactElement => {
  const { projectID, taskID } = useImbueParams();
  const [toast, setToast] = useState<ToastContent | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const textBlocks = message.content.filter((block: BlockUnion): block is TextBlock => isTextBlock(block));
  const fileBlocks = message.content.filter((block: BlockUnion): block is FileBlock => isFileBlock(block));

  let textContent = "";
  textBlocks.forEach((block) => {
    textContent += block.text;
  });

  const handleDelete = async (): Promise<void> => {
    if (!taskID || !projectID) {
      return;
    }

    setIsDeleting(true);
    try {
      await deleteMessage({
        path: { project_id: projectID, task_id: taskID, message_id: message.id },
      });
    } catch (error) {
      console.error("Failed to delete queued message:", error);
      setToast({ title: "Failed to delete queued message", type: ToastType.ERROR });
      setIsDeleting(false);
    }
  };

  const handleCopy = (): void => {
    navigator.clipboard.writeText(textContent);
    setToast({ title: "Message copied to clipboard", type: ToastType.SUCCESS });
  };

  return (
    <>
      <Box
        className={styles.queuedMessageCard}
        p="3"
        mb="3"
        ml="auto"
        mr="auto"
        width="80%"
        position="relative"
        data-testid={ElementIds.QUEUED_MESSAGE_CARD}
      >
        <Text className={styles.queuedLabel} mb="2" size="1" weight="bold">
          Queued Message
        </Text>
        <Box className={styles.queuedMessageContent}>
          <TipTapViewer content={textContent} />
        </Box>
        {fileBlocks.length > 0 && (
          <Box ml="-2">
            <FilePreviewList files={fileBlocks.map((block) => block.source)} />
          </Box>
        )}
        <IconButton
          variant="ghost"
          size="1"
          onClick={handleDelete}
          disabled={isDeleting}
          className={styles.deleteButton}
          data-testid={ElementIds.DELETE_QUEUED_MESSAGE_BUTTON}
          style={{
            position: "absolute",
            top: "var(--space-3)",
            right: "var(--space-3)",
          }}
        >
          {isDeleting ? <Spinner size="1" /> : <Trash2 size={16} />}
        </IconButton>
        <IconButton
          variant="ghost"
          size="1"
          onClick={handleCopy}
          className={styles.copyButton}
          title="Copy message"
          style={{
            position: "absolute",
            bottom: "var(--space-3)",
            right: "var(--space-3)",
          }}
        >
          <CopyIcon size={16} />
        </IconButton>
      </Box>
      <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} title={toast?.title} type={toast?.type} />
    </>
  );
};
