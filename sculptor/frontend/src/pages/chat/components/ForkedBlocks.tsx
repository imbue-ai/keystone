import { Box, Button, Flex, Text, Tooltip } from "@radix-ui/themes";
import { LocateFixedIcon, SplitIcon } from "lucide-react";
import type { ReactElement } from "react";

import { ElementIds } from "~/api";
import { useImbueNavigate, useProjectPageParams } from "~/common/NavigateUtils.ts";
import { useTask } from "~/common/state/hooks/useTaskHelpers";
import type { TaskID } from "~/common/Types.ts";

import styles from "./ForkedBlocks.module.scss";

type ForkedToBlockProps = {
  forkedToTaskId: TaskID;
};

export const ForkedToBlockComponent = ({ forkedToTaskId }: ForkedToBlockProps): ReactElement => {
  const { projectID } = useProjectPageParams();
  const task = useTask(forkedToTaskId);
  const { navigateToChat } = useImbueNavigate();

  const navigateToForkedTask = (): void => {
    navigateToChat(projectID, forkedToTaskId);
  };

  // This might happen if the task was deleted
  // TODO: Consider showing a message that the task was deleted
  if (!task) {
    return <></>;
  }

  const displayTitle = task.title || "Untitled task";
  const displayBranchName = task.branchName || "Generating branch";

  return (
    <Box maxWidth="100%" mt="4" data-testid={ElementIds.FORKED_TO_BLOCK} className={styles.outerBlock} px="3" py="2">
      <Flex className={styles.contentWrapper} align="center" justify="between">
        <Flex className={styles.leftSection}>
          <SplitIcon className={styles.forkIcon} size={16} />
          <Tooltip content={displayTitle}>
            <Text size="2" className={styles.titleText}>
              {displayTitle}
            </Text>
          </Tooltip>
        </Flex>
        <Flex className={styles.rightSection}>
          <Text size="2" className={styles.branchName}>
            {displayBranchName}
          </Text>
          <Button onClick={navigateToForkedTask} size="1" data-testid={ElementIds.FORK_BLOCK_BUTTON}>
            View Fork
          </Button>
        </Flex>
      </Flex>
    </Box>
  );
};

type ForkedFromBlockProps = {
  forkedFromTaskId: TaskID;
};

export const ForkedFromBlockComponent = ({ forkedFromTaskId }: ForkedFromBlockProps): ReactElement => {
  const { projectID } = useProjectPageParams();
  const task = useTask(forkedFromTaskId);
  const { navigateToChat } = useImbueNavigate();

  const navigateToForkedTask = (): void => {
    navigateToChat(projectID, forkedFromTaskId);
  };

  // This might happen if the task was deleted
  // TODO: Consider showing a message that the task was deleted
  if (!task) {
    return <></>;
  }

  const displayTitle = task.title || "Untitled task";
  const displayBranchName = task.branchName || "Generating branch";

  return (
    <Box maxWidth="100%" data-testid={ElementIds.FORKED_FROM_BLOCK} className={styles.outerBlock} py="2" px="3">
      <Flex className={styles.contentWrapper}>
        <Flex className={styles.leftSection}>
          <LocateFixedIcon className={styles.forkIcon} size={16} />
          <Tooltip content={displayTitle}>
            <Text size="2" className={styles.titleText}>
              {displayTitle}
            </Text>
          </Tooltip>
        </Flex>
        <Flex className={styles.rightSection}>
          <Text size="2" className={styles.branchName}>
            {displayBranchName}
          </Text>
          <Button onClick={navigateToForkedTask} size="1" data-testid={ElementIds.FORK_BLOCK_BUTTON}>
            View Parent
          </Button>
        </Flex>
      </Flex>
    </Box>
  );
};
