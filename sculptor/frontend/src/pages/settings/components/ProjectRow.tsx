import { Box, Button, Flex, Text, Tooltip } from "@radix-ui/themes";
import { AlertTriangle } from "lucide-react";
import type { ReactElement } from "react";

import styles from "./ProjectRow.module.scss";

type ProjectRowProps = {
  projectName: string;
  projectPath: string;
  activeAgentCount: number;
  archivedAgentCount: number;
  isPathAccessible: boolean;
  onRemove: () => void;
};

export const ProjectRow = ({
  projectName,
  projectPath,
  activeAgentCount,
  archivedAgentCount,
  isPathAccessible,
  onRemove,
}: ProjectRowProps): ReactElement => {
  return (
    <Box className={styles.projectRow}>
      <Flex direction="column" className={styles.projectInfo}>
        <Flex align="center" className={styles.projectName}>
          <Text weight="medium">{projectName}</Text>
          {!isPathAccessible && (
            <Tooltip content="This repository path cannot be found">
              <AlertTriangle size={16} className={styles.warningIcon} />
            </Tooltip>
          )}
        </Flex>
        <Text className={styles.projectDetails}>
          <span className={styles.projectPath}>{projectPath}</span> —{" "}
          <span className={styles.agentCounts}>
            {activeAgentCount} active agent{activeAgentCount !== 1 ? "s" : ""}, {archivedAgentCount} archived
          </span>
        </Text>
      </Flex>
      <Button variant="solid" color="red" className={styles.removeButton} onClick={onRemove}>
        Remove repo & agents
      </Button>
    </Box>
  );
};
