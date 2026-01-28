import { Box, Flex, Text } from "@radix-ui/themes";
import type { ReactElement } from "react";

import { ToolBlock, ToolBlockHTML } from "~/components/ToolBlock.tsx";

import type { ActionOutput, ToolResultBlock } from "../../../../api";
import { ElementIds } from "../../../../api";
import {
  isCommandHTMLOutput,
  isCommandTextOutput,
  isErroredOutput,
  isImbueCliToolContent,
} from "../../../../common/Guards.ts";
import { neutral } from "../../../../common/Utils.ts";
import styles from "./ImbueCLIToolBlock.module.scss";

type ImbueCLIToolBlockProps = {
  toolResult: ToolResultBlock;
};

/**
 * Renders the content for an MCP CLI tool action output
 */
function renderActionOutput(outputToolContent: ActionOutput): JSX.Element {
  console.log("outputToolContent", outputToolContent);
  if (isErroredOutput(outputToolContent.userDisplay)) {
    return (
      <ToolBlock
        invocationString={outputToolContent.command}
        content={outputToolContent.userDisplay.errorMessage}
        isError={true}
      />
    );
  }

  if (isCommandTextOutput(outputToolContent.userDisplay)) {
    return (
      <ToolBlock
        invocationString={outputToolContent.command}
        content={outputToolContent.userDisplay.output}
        isError={false}
      />
    );
  }

  if (isCommandHTMLOutput(outputToolContent.userDisplay)) {
    return (
      <ToolBlockHTML invocationString={outputToolContent.command} content={outputToolContent.userDisplay.output} />
    );
  }

  // Final fallback: show the entire JSON content
  return (
    <ToolBlock
      invocationString={outputToolContent.command}
      content={JSON.stringify(outputToolContent, null, 2)}
      isError={false}
    />
  );
}

export const ImbueCLIToolBlock = ({ toolResult }: ImbueCLIToolBlockProps): ReactElement => {
  if (!isImbueCliToolContent(toolResult.content)) {
    console.error("Invalid Imbue CLI tool content:", JSON.stringify(toolResult));
    return <Text color={neutral}>Invalid Imbue CLI tool content</Text>;
  }

  const imbueCLIContent = toolResult.content;
  if (!imbueCLIContent.actionOutputs || imbueCLIContent.actionOutputs.length === 0) {
    return (
      <Flex direction="column" maxWidth="100%" className={styles.allIssuesContainer}>
        <Box px="2" pt="2" data-testid={ElementIds.NO_ACTION_OUTPUTS}>
          <Text color={neutral}>No action outputs available.</Text>
        </Box>
      </Flex>
    );
  }

  // Handle single vs multiple action outputs
  if (imbueCLIContent.actionOutputs.length === 1) {
    // Single action: render directly without extra wrapper
    return renderActionOutput(imbueCLIContent.actionOutputs[0]);
  } else {
    // Multiple actions: render each in separate flex boxes
    return (
      <Flex direction="column" gap="2" maxWidth="100%">
        {imbueCLIContent.actionOutputs.map((outputToolContent, index) => (
          <Box key={index} className={styles.allIssuesContainer}>
            <Box px="2" py="1" style={{ backgroundColor: "var(--gold-2)", borderBottom: "1px solid var(--gold-4)" }}>
              <Text size="1" weight="medium">
                Action {index + 1}: {outputToolContent.command}
              </Text>
            </Box>
            <Box style={{ padding: 0 }}>{renderActionOutput(outputToolContent)}</Box>
          </Box>
        ))}
      </Flex>
    );
  }
};
