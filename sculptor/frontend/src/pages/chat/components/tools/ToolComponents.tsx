import { Badge, Box, Code, Flex, IconButton, Text } from "@radix-ui/themes";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { ReactElement } from "react";
import { useState } from "react";

import type { ToolResultBlock, ToolUseBlock } from "../../../../api";
import { ElementIds } from "../../../../api";
import { isGenericToolContent } from "../../../../common/Guards.ts";
import { mergeClasses } from "../../../../common/Utils";
import { ToolBlock } from "../../../../components/ToolBlock";
import { getToolDisplayName, getToolDisplayNamePresent, isDiffTool, isImbueCLITool } from "../../utils/utils";
import { DiffToolBlock } from "./DiffToolBlock";
import { ImbueCLIToolBlock } from "./ImbueCLIToolBlock";
import toolStyles from "./ToolComponents.module.scss";

type CollapsibleToolSectionProps = {
  toolBlocks: Array<ToolUseBlock | ToolResultBlock>;
  isActive?: boolean;
};

export const CollapsibleToolSection = ({ toolBlocks, isActive = false }: CollapsibleToolSectionProps): ReactElement => {
  const [isExpanded, setIsExpanded] = useState(false);

  // For single tool, render it directly (still collapsible)
  if (toolBlocks.length === 1) {
    return <ToolDisplay toolBlock={toolBlocks[0]} />;
  }

  // Get the current tool being processed (the only tool_use block without a result)
  const currentTool = isActive ? toolBlocks.find((block) => block.type === "tool_use") : null;
  const currentToolName =
    currentTool && currentTool.type === "tool_use" ? getToolDisplayNamePresent(currentTool.name) : null;

  // For multiple tools, show nested collapsible
  return (
    <Box maxWidth="100%">
      <Flex
        align="center"
        gapX="2"
        py="1"
        px="1"
        className={toolStyles.collapsibleHeader}
        maxWidth="100%"
        onClick={() => setIsExpanded(!isExpanded)}
        data-testid={ElementIds.TOOL_CALL}
      >
        <IconButton variant="ghost" size="1" className={toolStyles.chevronIcon}>
          {isExpanded ? <ChevronDown width={16} /> : <ChevronRight width={16} />}
        </IconButton>
        <Badge className={toolStyles.toolCountBadge}>{toolBlocks.length}</Badge>
        <Text>{isActive ? "Calling Tools" : "Called Tools"}</Text>
        {isActive && currentToolName && !isExpanded && (
          <Text size="1" className={mergeClasses(toolStyles.ghostText, toolStyles.currentToolName)}>
            {currentToolName}
          </Text>
        )}
      </Flex>

      {isExpanded && (
        <Box ml="5" maxWidth="100%" className={toolStyles.toolsContainer}>
          {toolBlocks.map((block, index) => (
            <ToolDisplay key={index} toolBlock={block} />
          ))}
        </Box>
      )}
    </Box>
  );
};

type ToolDisplayProps = {
  toolBlock: ToolUseBlock | ToolResultBlock;
};

const renderToolBlock = (toolResult: ToolResultBlock): ReactElement => {
  if (isGenericToolContent(toolResult.content)) {
    return (
      <ToolBlock
        invocationString={toolResult.invocationString}
        content={toolResult.content.text}
        isError={toolResult.isError}
      />
    );
  } else {
    return (
      <ToolBlock
        invocationString={toolResult.invocationString}
        content={JSON.stringify(toolResult.content, null, 2)}
        isError={toolResult.isError}
      />
    );
  }
};

const getToolName = (block: ToolUseBlock | ToolResultBlock): string => {
  return block.type === "tool_use" ? block.name : (block as ToolResultBlock).toolName;
};

const ToolDisplay = ({ toolBlock }: ToolDisplayProps): ReactElement => {
  const [isExpanded, setIsExpanded] = useState(false);

  const isToolResult = toolBlock.type === "tool_result";
  const toolName = getToolName(toolBlock);
  const displayName = isToolResult ? getToolDisplayName(toolName) : getToolDisplayNamePresent(toolName);

  let collapsedText;
  if (isToolResult) {
    collapsedText = toolBlock.invocationString;
  } else {
    collapsedText = null;
  }

  return (
    <Box maxWidth="100%" data-testid={ElementIds.TOOL_CALL}>
      <Flex direction="column" maxWidth="100%">
        <Flex
          align="center"
          gap="2"
          py="1"
          px="1"
          maxWidth="100%"
          className={toolStyles.toolHeader}
          onClick={() => setIsExpanded(!isExpanded)}
          data-testid={ElementIds.TOOL_HEADER}
        >
          <IconButton variant="ghost" size="1" className={toolStyles.chevronIcon}>
            {isExpanded ? <ChevronDown width={14} /> : <ChevronRight width={14} />}
          </IconButton>
          <Text size="2" className={toolStyles.toolDisplayName}>
            {displayName}
          </Text>
          {!isExpanded && collapsedText && (
            <Text size="1" className={toolStyles.ghostText} truncate={true}>
              {collapsedText}
            </Text>
          )}
        </Flex>

        {isExpanded && (
          <Box ml="4" mb="2" maxWidth="100%">
            {isToolResult ? (
              <>
                {isDiffTool(toolName) &&
                  (!toolBlock.isError && toolBlock.content.contentType === "diff" ? (
                    <DiffToolBlock toolResult={toolBlock} />
                  ) : (
                    renderToolBlock(toolBlock)
                  ))}
                {isImbueCLITool(toolName) && <ImbueCLIToolBlock toolResult={toolBlock} />}
                {!isDiffTool(toolName) && !isImbueCLITool(toolName) && renderToolBlock(toolBlock)}
              </>
            ) : (
              // Handle tool_use blocks (no result yet)
              <Code size="1" className={toolStyles.toolInput}>
                {JSON.stringify(toolBlock.input, null, 2)}
              </Code>
            )}
          </Box>
        )}
      </Flex>
    </Box>
  );
};
