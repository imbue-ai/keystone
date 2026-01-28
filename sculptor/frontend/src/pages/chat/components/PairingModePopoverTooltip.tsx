import { Box, Flex, Text } from "@radix-ui/themes";
import { useAtomValue } from "jotai";
import { ArchiveRestoreIcon, ArrowUpDownIcon, FileDownIcon } from "lucide-react";
import type { ReactElement } from "react";

import type { GitRepoStatus, SyncedTaskView } from "~/api";
import { isPairingModeStashingBetaFeatureOnAtom } from "~/common/state/atoms/userConfig";

import styles from "./SyncPopover.module.scss";

type PairingModePopoverTooltipProps = {
  task: SyncedTaskView;
  otherSyncedTaskBranchName: string | undefined | null;
  repoStatus: GitRepoStatus | undefined;
  projectID: string;
};

export const PairingModePopoverTooltip = ({
  task,
  repoStatus,
  otherSyncedTaskBranchName,
}: PairingModePopoverTooltipProps): ReactElement => {
  const isPairingModeStashingEnabled = useAtomValue(isPairingModeStashingBetaFeatureOnAtom);
  const isOtherTaskSynced =
    typeof otherSyncedTaskBranchName === "string" && otherSyncedTaskBranchName != task.branchName;

  if (isOtherTaskSynced) {
    return (
      <Box p="4">
        <Text size="2" weight="medium">
          Switching to this Task will...
        </Text>
        <Box>
          <Text size="1" color="gray" as="div">
            <ol className={styles.popoverNumberedList}>
              <li>Disable pairing on {otherSyncedTaskBranchName}</li>
              <li>Check out {task.branchName}</li>
              <li>Begin bidirectional sync</li>
            </ol>
          </Text>
        </Box>
      </Box>
    );
  }

  const wouldStashNote =
    repoStatus === undefined
      ? "(git state loading)"
      : repoStatus.files.areCleanIncludingUntracked
        ? "(index currently clean)"
        : `(currently ${repoStatus.files.description.split("\n").join(", ")})`;

  return (
    <Flex direction="column" gap="3" p="5">
      <Text size="3" weight="medium">
        {isOtherTaskSynced ? "Switch to this Task" : "Pairing Mode Quick Start"}
      </Text>

      <Flex direction="column" gap="3">
        {isOtherTaskSynced ? null : (
          <>
            <Box className={styles.gridItem}>
              <Box className={styles.popoverIconWrapper}>
                <FileDownIcon size={16} />
              </Box>
              <Text size="2" weight="medium">
                Bring the agent&#39;s work to you
              </Text>
              <Text size="1" color="gray">
                This live-mirrors files &amp; git state between the Docker container and your local repo so that you can
                pair with the agent from your IDE.
              </Text>
            </Box>
            <Box className={styles.gridItem}>
              <Box className={styles.popoverIconWrapper}>
                <ArchiveRestoreIcon size={16} />
              </Box>
              <Text size="2" weight="medium">
                Don&#39;t interrupt your local work
              </Text>
              {isPairingModeStashingEnabled ? (
                <Text size="1" color="gray">
                  When you turn off Pairing Mode, your prior local files &amp; git state will be restored so you can
                  pick up where you left off
                </Text>
              ) : (
                <Text size="1" color="gray">
                  When you turn off Pairing Mode, your prior git state will be restored so you can pick up where you
                  left off
                </Text>
              )}
            </Box>
          </>
        )}

        <Box className={styles.gridItem}>
          <Box className={styles.popoverIconWrapper}>
            <ArrowUpDownIcon size={16} />
          </Box>
          <Text size="2" weight="medium">
            Enabling Pairing Mode will...
          </Text>
          <Box>
            <Text size="1" color="gray" as="div">
              <ol className={styles.popoverNumberedList}>
                {isPairingModeStashingEnabled && <li>Stash your current work if necessary {wouldStashNote}</li>}
                <li>Check out {task.branchName}</li>
                <li>Begin bidirectional sync</li>
              </ol>
            </Text>
          </Box>
        </Box>
      </Flex>
    </Flex>
  );
};
