import { Flex, Text } from "@radix-ui/themes";
import { FileText } from "lucide-react";
import { type ReactElement, useMemo } from "react";

import { ArtifactType } from "../../../../api";
import type { ArtifactViewContentProps, ArtifactViewTabLabelProps } from "../../Types.ts";
import { parseLogLine } from "./LogArtifactViewUtils";

const renderLogLine = (line: string, index: number): ReactElement => {
  const { prefix, content, isError } = parseLogLine(line);
  return (
    <Text key={index} size="2" style={{ fontFamily: "monospace", whiteSpace: "nowrap" }}>
      {prefix && <span style={{ color: isError ? "var(--red-9)" : "var(--gold-9)" }}>{prefix}</span>}
      {content && <span>{content}</span>}
    </Text>
  );
};

export const LogsArtifactViewComponent = ({ artifacts }: ArtifactViewContentProps): ReactElement => {
  const logsArtifact = artifacts[ArtifactType.LOGS];
  const component = useMemo((): ReactElement => {
    const logs = logsArtifact?.logs || [];

    if (!logs.length) {
      return <Text color="gray">No logs available</Text>;
    }

    return (
      <Flex direction="column" gap="1" style={{ overflowX: "auto" }}>
        {logs.map(renderLogLine)}
      </Flex>
    );
  }, [logsArtifact]);

  return component;
};

export const LogsTabLabelComponent = ({ shouldShowIcon = true }: ArtifactViewTabLabelProps): ReactElement => {
  return (
    <Flex align="center" gap="2">
      {shouldShowIcon && <FileText />}
      Logs
    </Flex>
  );
};
