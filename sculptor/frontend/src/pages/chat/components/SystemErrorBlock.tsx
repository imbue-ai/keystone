import { Badge, Box, Button, Flex, IconButton, ScrollArea, Text, Tooltip } from "@radix-ui/themes";
import { atom, useAtom } from "jotai";
import { ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import type { ReactElement } from "react";
import { useState } from "react";

import { ElementIds, TaskStatus } from "../../../api";
import styles from "./SystemErrorBlock.module.scss";

// Set of message IDs that have been retried
const retriedMessageIdsAtom = atom<Set<string>>(new Set<string>());

type SystemErrorBlockProps = {
  errorType: string;
  content: string;
  message: string;
  messageId: string;
  taskStatus: TaskStatus;
  onRetryRequest: () => void;
  showRetryButton?: boolean;
};

export const SystemErrorBlock = ({
  errorType,
  content,
  message,
  messageId,
  taskStatus,
  onRetryRequest,
  showRetryButton = true,
}: SystemErrorBlockProps): ReactElement => {
  const [isExpanded, setIsExpanded] = useState(false);
  const [retriedMessageIds, setRetriedMessageIds] = useAtom(retriedMessageIdsAtom);

  const hasBeenRetried = retriedMessageIds.has(messageId);

  const shouldDisplayMessage = (msg: string | number): boolean => {
    return typeof msg === "string" && msg.length != 0;
  };

  return (
    <Flex direction="column">
      <Flex
        align="center"
        gap="2"
        px="3"
        py="2"
        className={`${styles.header} ${!isExpanded ? styles.headerCollapsed : ""}`}
        onClick={() => setIsExpanded(!isExpanded)}
        data-testid={ElementIds.ERROR_BLOCK}
      >
        <IconButton variant="ghost" size="1" className={styles.chevronIcon}>
          {isExpanded ? <ChevronDown /> : <ChevronRight />}
        </IconButton>
        <Badge className={styles.errorBadge} size="1" variant="soft">
          {errorType ? errorType.split(".").pop() : "Request Failed"}
        </Badge>
        <Text size="2" truncate={true}>
          {shouldDisplayMessage(message) ? message : "Unknown error"}
        </Text>
      </Flex>
      {isExpanded && (
        <Box className={styles.body} maxHeight="400px">
          <ScrollArea className={styles.scrollArea} scrollbars="vertical">
            <Box px="3" py="2">
              <pre className={styles.traceback}>{content}</pre>
            </Box>
          </ScrollArea>
        </Box>
      )}
      <Flex gap="2" py="2" justify="start">
        {/* TODO: only enable retry request button when errorType.includes("ClaudeTransientError") */}
        {taskStatus !== TaskStatus.ERROR &&
          showRetryButton &&
          (hasBeenRetried ? (
            <Tooltip content="This request has already been retried.">
              <span>
                <Button
                  size="1"
                  variant="solid"
                  color="red"
                  disabled={true}
                  data-testid={ElementIds.ERROR_BLOCK_RETRY_BUTTON}
                >
                  Retry Request
                  <RefreshCw size={14} />
                </Button>
              </span>
            </Tooltip>
          ) : (
            <Button
              size="1"
              variant="solid"
              color="red"
              onClick={(e) => {
                e.stopPropagation();
                setRetriedMessageIds(new Set([...retriedMessageIds, messageId]));
                onRetryRequest();
              }}
              data-testid={ElementIds.ERROR_BLOCK_RETRY_BUTTON}
            >
              Retry Request
              <RefreshCw size={14} />
            </Button>
          ))}
      </Flex>
    </Flex>
  );
};
