import { Box, ScrollArea, Text } from "@radix-ui/themes";
import type { ReactElement } from "react";
import { useEffect, useMemo, useState } from "react";

import type { ToolResultBlock } from "../../../../api";
import { isDiffToolContent } from "../../../../common/Guards.ts";
import { neutral } from "../../../../common/Utils.ts";
import { parseDiff } from "../../../../components/DiffUtils";
import { SingleFileDiff } from "../../../../components/SingleFileDiff";

type DiffToolBlockProps = {
  toolResult: ToolResultBlock;
};

export const DiffToolBlock = ({ toolResult }: DiffToolBlockProps): ReactElement | undefined => {
  const [visibleStates, setVisibleStates] = useState<Array<boolean>>([]);

  const diffStrings = useMemo(() => {
    if (!toolResult.content || toolResult.content.contentType !== "diff") return [];
    if (!isDiffToolContent(toolResult.content)) {
      console.error("Expected tool result content to be of type 'diff', but got:", toolResult.content);
      return [];
    }
    const { diffStrings } = parseDiff(toolResult.content.diff);
    return diffStrings;
  }, [toolResult?.content]);

  useEffect(() => {
    setVisibleStates(diffStrings.map(() => true));
  }, [diffStrings]);

  if (!toolResult.content || toolResult.content.contentType !== "diff") {
    return (
      <Box pl="2" pt="1">
        <Text color={neutral}>Failed to generate a diff.</Text>
      </Box>
    );
  }

  return (
    <ScrollArea>
      <Box maxHeight="500px">
        {diffStrings.map((diff, index) => (
          <SingleFileDiff
            key={index}
            diffString={diff}
            isBodyVisible={visibleStates[index] || false}
            onToggleBodyVisible={() => {
              setVisibleStates((prev) => {
                const newStates = [...prev];
                newStates[index] = !newStates[index];
                return newStates;
              });
            }}
          />
        ))}
      </Box>
    </ScrollArea>
  );
};
