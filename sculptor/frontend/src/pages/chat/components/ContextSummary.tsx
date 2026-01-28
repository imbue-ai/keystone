import { Box, Flex, IconButton, Text } from "@radix-ui/themes";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { ReactElement } from "react";
import { useState } from "react";

import { ElementIds } from "../../../api";
import { MarkdownBlock } from "../../../components/MarkdownBlock";
import styles from "./tools/ToolComponents.module.scss";

type ContextSummaryProps = {
  message: string;
};

export const ContextSummary = ({ message }: ContextSummaryProps): ReactElement => {
  const [isExpanded, setIsExpanded] = useState(false);

  return (
    <Box maxWidth="100%" data-testid={ElementIds.CONTEXT_SUMMARY}>
      <Flex direction="column" maxWidth="100%">
        <Flex
          align="center"
          gap="2"
          py="1"
          maxWidth="100%"
          className={styles.toolHeader}
          onClick={() => setIsExpanded(!isExpanded)}
          data-testid={ElementIds.CONTEXT_SUMMARY_HEADER}
        >
          <IconButton variant="ghost" size="1" className={styles.chevronIcon}>
            {isExpanded ? <ChevronDown width={14} /> : <ChevronRight width={14} />}
          </IconButton>
          <Text size="2">Context Compacted</Text>
          {!isExpanded && (
            <Text size="1" className={styles.ghostText} truncate={true}>
              summary
            </Text>
          )}
        </Flex>

        {isExpanded && (
          <Box ml="4" mb="2" maxWidth="100%">
            <MarkdownBlock content={message} />
          </Box>
        )}
      </Flex>
    </Box>
  );
};
