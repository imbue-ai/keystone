import { Badge, Box, Flex, IconButton, ScrollArea, Text } from "@radix-ui/themes";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { ReactElement } from "react";
import { useState } from "react";

import styles from "./SystemWarningBlock.module.scss";

type SystemWarningBlockProps = {
  warningType: string;
  content?: string | null;
  message: string;
};

export const SystemWarningBlock = ({ warningType, content, message }: SystemWarningBlockProps): ReactElement => {
  const [isExpanded, setIsExpanded] = useState(false);

  const shouldDisplayMessage = (msg: string | number): boolean => {
    return typeof msg === "string" && msg.length != 0;
  };

  const hasContent = content && content.trim().length > 0;

  return (
    <Flex direction="column" className={styles.container}>
      <Flex
        align="center"
        gap="2"
        px="3"
        py="2"
        className={`${styles.header} ${!isExpanded ? styles.headerCollapsed : ""}`}
        onClick={hasContent ? (): void => setIsExpanded(!isExpanded) : undefined}
        style={{ cursor: hasContent ? "pointer" : "default" }}
      >
        {hasContent && (
          <IconButton variant="ghost" size="1" className={styles.chevronIcon}>
            {isExpanded ? <ChevronDown /> : <ChevronRight />}
          </IconButton>
        )}
        <Badge className={styles.warningBadge} size="1" variant="soft">
          {warningType ? warningType.split(".").pop() : "Warning"}
        </Badge>
        <Text size="2" style={{ wordBreak: "break-word" }}>
          {shouldDisplayMessage(message) ? message : "Unknown warning"}
        </Text>
      </Flex>
      {isExpanded && hasContent && (
        <Box className={styles.body} maxHeight="400px">
          <ScrollArea className={styles.scrollArea} scrollbars="vertical">
            <Box px="3" py="2">
              <pre className={styles.traceback}>{content}</pre>
            </Box>
          </ScrollArea>
        </Box>
      )}
    </Flex>
  );
};
